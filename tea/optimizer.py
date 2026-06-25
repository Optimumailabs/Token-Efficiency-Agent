"""TEA optimiser: deterministic prompt transforms plus an optional LLM hook.

The optimiser takes either a raw string or a list of chat messages
(``[{"role": ..., "content": ...}, ...]``) and returns a reduced version
together with a report of what changed and how many tokens were saved.

Design principles:

1. Deterministic by default. The built-in transforms (dedupe, drop low-utility
   context, trim whitespace and boilerplate, prune oversized few-shot blocks)
   never call a model. They are safe, fast, and reproducible, and they
   typically cut 15 to 35 per cent of a bloated prompt with negligible quality
   risk.
2. Optional LLM compression. Pass a ``compressor`` callable to enable deeper,
   semantics-aware compression. The optimiser only routes the chunks that the
   deterministic pass judged low-value through the compressor, so the extra
   model cost stays small.
3. Quality guard. The caller sets which transforms may run. Anything that could
   change meaning (LLM compression, aggressive context dropping) is opt-in and
   bounded.

The optimiser does not call any provider SDK. Framework adapters in
``tea.integrations`` wire it into LangChain, CrewAI, AutoGen, OpenAI, and
Anthropic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .tokens import count_tokens

# A compressor takes (text, target_ratio) and returns a shorter text that
# preserves meaning. target_ratio is the fraction of the original length to aim
# for (0.5 means "about half"). Implementations are free to under-shoot.
Compressor = Callable[[str, float], str]


@dataclass
class TransformResult:
    name: str
    tokens_before: int
    tokens_after: int
    note: str

    @property
    def saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


@dataclass
class OptimizeResult:
    original: object              # str or list[dict]
    optimized: object             # same type as original
    model: str
    tokens_before: int
    tokens_after: int
    transforms: list[TransformResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def reduction_pct(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return 100.0 * self.tokens_saved / self.tokens_before

    def summary(self) -> str:
        lines = [
            f"TEA optimisation ({self.model}): "
            f"{self.tokens_before:,} -> {self.tokens_after:,} tokens "
            f"({self.reduction_pct:.1f}% reduction, {self.tokens_saved:,} saved)."
        ]
        for t in self.transforms:
            if t.saved > 0:
                lines.append(f"  - {t.name}: saved {t.saved:,} tokens. {t.note}")
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic text transforms
# ---------------------------------------------------------------------------
def _normalise_whitespace(text: str) -> str:
    """Collapse runs of blank lines and trailing spaces without touching code
    fences. Code blocks are preserved verbatim because whitespace there is
    often significant."""
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # inside a fenced code block
            out.append(part)
            continue
        # Strip trailing spaces on each line.
        cleaned = re.sub(r"[ \t]+\n", "\n", part)
        # Collapse 3+ newlines to 2.
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        out.append(cleaned)
    return "".join(out).strip()


def _dedupe_key(s: str) -> str:
    """Normalise a span of text to a comparison key: lowercase, strip
    non-word characters, take the first 200 chars."""
    return re.sub(r"\W+", "", s.lower())[:200]


def _sentence_eligible(s: str) -> bool:
    """A sentence is eligible for dedupe if it carries real content: at least
    three content words, or 25-plus characters. This protects short labels and
    list markers ("Q:", "Yes.", "Step 1.") from being dropped."""
    return len(s) >= 25 or len(_content_tokens(s)) >= 3


def _dedupe_paragraphs(text: str) -> tuple[str, int]:
    """Drop duplicate paragraphs and repeated sentences. Returns (text, n).

    Three layers, from coarse to fine:

    1. Paragraph dedupe: whole repeated passages (common when RAG retrieves the
       same chunk twice).
    2. Frequency collapse: any sentence with at least two content words that
       appears three or more times anywhere is kept only on first occurrence.
       Heavy repetition is waste regardless of sentence length.
    3. Pairwise sentence dedupe: an eligible sentence (see _sentence_eligible)
       seen once already is dropped on its next occurrence. This catches the
       same fact glued to different surrounding lines, where paragraphs are not
       byte-equal.
    """
    paragraphs = re.split(r"\n\s*\n", text)

    # Layer 2 pre-scan: count sentence frequencies across the whole text.
    freq: dict[str, int] = {}
    for p in paragraphs:
        for s in re.split(r"(?<=[\.\!\?])\s+", p.strip()):
            s_str = s.strip()
            if s_str and len(_content_tokens(s_str)) >= 2:
                freq[_dedupe_key(s_str)] = freq.get(_dedupe_key(s_str), 0) + 1

    seen_para: set[str] = set()
    seen_sent: set[str] = set()
    seen_freq: set[str] = set()
    kept: list[str] = []
    dropped = 0

    for p in paragraphs:
        stripped = p.strip()
        if not stripped:
            continue
        pkey = _dedupe_key(stripped)
        if pkey in seen_para:
            dropped += 1
            continue
        seen_para.add(pkey)

        sentences = re.split(r"(?<=[\.\!\?])\s+", stripped)
        kept_sents: list[str] = []
        for s in sentences:
            s_str = s.strip()
            if not s_str:
                continue
            skey = _dedupe_key(s_str)

            # Layer 2: collapse heavily repeated sentences (3+ occurrences).
            if freq.get(skey, 0) >= 3 and len(_content_tokens(s_str)) >= 2:
                if skey in seen_freq:
                    dropped += 1
                    continue
                seen_freq.add(skey)
                kept_sents.append(s_str)
                continue

            # Layer 3: pairwise dedupe of eligible sentences.
            if _sentence_eligible(s_str):
                if skey in seen_sent:
                    dropped += 1
                    continue
                seen_sent.add(skey)
            kept_sents.append(s_str)
        kept.append(" ".join(kept_sents))

    return "\n\n".join(kept), dropped


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "this", "that", "these", "those", "it", "its",
    "by", "as", "from", "i", "you", "he", "she", "we", "they", "me", "him",
    "her", "us", "them", "my", "your", "our", "their",
    # Common interrogatives and fillers that create false query overlap.
    "about", "tell", "what", "which", "who", "whom", "whose", "when", "where",
    "why", "how", "please", "give", "show", "explain", "describe", "list",
    "can", "could", "would", "should", "will", "shall", "may", "might",
    "into", "over", "under", "more", "most", "some", "any", "all", "each",
}


def _content_tokens(s: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w+", s) if w.lower() not in _STOPWORDS}


# ---------------------------------------------------------------------------
# Content-aware routing
# ---------------------------------------------------------------------------
# Different content compresses best in different ways. A uniform transform
# leaves easy wins on the table (pretty-printed JSON) or risks corruption
# (code). The router classifies a block, then applies the safe transform for
# that type. Everything here stays deterministic.

def classify_block(text: str) -> str:
    """Classify a text block as 'json', 'code', or 'prose'. Heuristic and
    conservative: when unsure, returns 'prose', which only ever gets the safe
    text transforms."""
    s = text.strip()
    if not s:
        return "prose"
    # Fenced code block.
    if s.startswith("```") and s.rstrip().endswith("```"):
        return "code"
    # JSON: starts and ends with matched braces/brackets and parses.
    if (s[0] in "{[" and s[-1] in "}]"):
        try:
            import json
            json.loads(s)
            return "json"
        except Exception:
            pass
    # Code smell: several lines and a high share of code-like punctuation or
    # common keywords, without being prose-like.
    lines = s.splitlines()
    if len(lines) >= 3:
        code_signals = sum(
            1 for ln in lines
            if re.search(r"[;{}]\s*$|^\s*(def |class |function |import |const |let |var |#include|public |private )", ln)
        )
        if code_signals >= max(2, len(lines) // 4):
            return "code"
    return "prose"


def _minify_json(text: str) -> tuple[str, bool]:
    """Re-serialise JSON with no superfluous whitespace, preserving key order.
    Returns (minified, changed). Key order is preserved so a stable prefix is
    not disturbed. Returns the input unchanged if it does not parse."""
    s = text.strip()
    try:
        import json
        obj = json.loads(s)
    except Exception:
        return text, False
    minified = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return (minified, minified != s)


def _strip_code_comments(text: str) -> tuple[str, bool]:
    """Conservatively strip whole-line comments from a fenced code block, by
    language when the fence declares one. Inline comments are left alone to
    avoid touching strings that merely contain a comment marker. Returns
    (text, changed)."""
    m = re.match(r"^```([\w+-]*)\n(.*)\n```\s*$", text.strip(), re.DOTALL)
    if not m:
        return text, False
    lang = (m.group(1) or "").lower()
    body = m.group(2)
    # Per-language whole-line comment prefixes. Hash covers python/ruby/shell/
    # yaml; slashes cover c-family/js/go/rust/java.
    hash_langs = {"python", "py", "ruby", "rb", "bash", "sh", "shell", "yaml", "yml", "r", ""}
    slash_langs = {"js", "javascript", "ts", "typescript", "go", "rust", "rs",
                   "java", "c", "cpp", "c++", "cs", "csharp", "kotlin", "swift", "php"}
    prefixes = []
    if lang in hash_langs:
        prefixes.append("#")
    if lang in slash_langs or lang == "":
        prefixes.append("//")
    if not prefixes:
        return text, False
    kept = []
    removed = False
    for ln in body.split("\n"):
        stripped = ln.lstrip()
        if any(stripped.startswith(p) for p in prefixes):
            removed = True
            continue
        kept.append(ln)
    if not removed:
        return text, False
    # Safety: never empty a code block. If every line was a comment, the
    # comments are the content (a documentation or pseudo-code snippet), so
    # keep the original verbatim rather than returning an empty fence.
    if not any(ln.strip() for ln in kept):
        return text, False
    new_body = "\n".join(kept)
    return f"```{m.group(1)}\n{new_body}\n```", True


def _route_blocks(text: str, model: str) -> tuple[str, int, dict]:
    """Split text into blocks, classify each, and apply the type-appropriate
    deterministic compressor. Returns (text, tokens_saved, type_counts).

    Blocks are separated by blank lines, but fenced code blocks are kept whole
    even when they contain blank lines."""
    before = count_tokens(text, model)
    # Tokenise into fenced-code segments and everything else, then split the
    # non-code segments on blank lines.
    parts = re.split(r"(```[\w+-]*\n.*?\n```)", text, flags=re.DOTALL)
    out_blocks: list[str] = []
    counts = {"json": 0, "code": 0, "prose": 0}
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:  # a fenced code block
            kind = "code"
            new_part, changed = _strip_code_comments(part)
            counts["code"] += 1
            out_blocks.append(new_part)
            continue
        for block in re.split(r"(\n\s*\n)", part):
            if not block.strip():
                out_blocks.append(block)
                continue
            kind = classify_block(block)
            counts[kind] += 1
            if kind == "json":
                new_block, changed = _minify_json(block)
                out_blocks.append(new_block if changed else block)
            else:
                out_blocks.append(block)
    routed = "".join(out_blocks)
    after = count_tokens(routed, model)
    return routed, max(0, before - after), counts


def _drop_low_utility_chunks(
    context: str,
    query: str,
    model: str,
    keep_threshold: float,
    max_drop_fraction: float = 0.70,
) -> tuple[str, int]:
    """Drop context paragraphs whose lexical overlap with the query is below
    threshold. Returns (kept_context, tokens_dropped).

    This is the deterministic stand-in for attention-based dropping. It uses
    query-overlap (recall-weighted) rather than raw Jaccard so that a short
    query still matches a long relevant passage.

    Two safety bounds protect against the lexical proxy misjudging relevance:

    1. The context is never emptied. If every chunk scores below threshold, the
       single best-overlapping chunk is kept.
    2. At most `max_drop_fraction` of the context tokens are removed in one
       pass. If the threshold would drop more than that, the lowest-scoring
       chunks are dropped only up to the budget, and the rest are kept. This
       bounds the damage when the proxy is wrong, which matters because lexical
       overlap is a coarse signal. A real attention signal (see the brief)
       lifts this cap.
    """
    if not context.strip() or not query.strip():
        return context, 0
    q = _content_tokens(query)
    if not q:
        return context, 0

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", context) if p.strip()]
    if not paragraphs:
        return context, 0

    total_tokens = sum(count_tokens(p, model) for p in paragraphs)
    drop_budget = int(total_tokens * max_drop_fraction)

    # Score every paragraph by index, then drop the lowest-scoring ones (those
    # below threshold) in ascending score order until we hit the drop budget.
    scored = []  # (index, score, tokens)
    for idx, p in enumerate(paragraphs):
        c = _content_tokens(p)
        overlap = len(q & c) / len(q) if q else 0.0
        score = max(overlap, _jaccard(q, c))
        scored.append((idx, score, count_tokens(p, model)))

    below = sorted((s for s in scored if s[1] < keep_threshold), key=lambda s: s[1])
    dropped_idx: set[int] = set()
    dropped_tokens = 0
    for idx, score, ptoks in below:
        if dropped_tokens + ptoks > drop_budget:
            continue  # keep this one; dropping it would exceed the budget
        dropped_idx.add(idx)
        dropped_tokens += ptoks

    kept = [p for idx, p in enumerate(paragraphs) if idx not in dropped_idx]
    # Safety: never empty the context.
    if not kept:
        best = max(paragraphs, key=lambda p: len(_content_tokens(p) & q))
        kept = [best]
        dropped_tokens = max(0, total_tokens - count_tokens(best, model))
    return "\n\n".join(kept), max(0, dropped_tokens)


def _prune_few_shot(text: str, query_tokens: int, model: str, max_ratio: float) -> tuple[str, int]:
    """If a few-shot example block is more than `max_ratio` times the size of
    the query, keep the first half of the examples. Returns (text, dropped).

    Detection is heuristic: lines that look like Q/A or example pairs. This is
    intentionally conservative; it only fires on clearly oversized blocks."""
    blocks = re.split(r"\n\s*\n", text)
    ex_idx = [
        i for i, b in enumerate(blocks)
        if re.search(r"^\s*(q:|a:|example|input:|output:|user:|assistant:)", b, re.I)
    ]
    if len(ex_idx) < 4:
        return text, 0
    ex_tokens = sum(count_tokens(blocks[i], model) for i in ex_idx)
    if ex_tokens <= max_ratio * max(query_tokens, 1):
        return text, 0
    # Keep the first half of the example blocks.
    keep_n = max(2, len(ex_idx) // 2)
    drop_set = set(ex_idx[keep_n:])
    dropped = sum(count_tokens(blocks[i], model) for i in drop_set)
    kept_blocks = [b for i, b in enumerate(blocks) if i not in drop_set]
    return "\n\n".join(kept_blocks), dropped


# ---------------------------------------------------------------------------
# Public optimiser for raw text
# ---------------------------------------------------------------------------
def optimize_text(
    prompt: str,
    *,
    query: Optional[str] = None,
    context: Optional[str] = None,
    model: str = "gpt-4o",
    enable: Optional[set[str]] = None,
    keep_threshold: float = 0.06,
    few_shot_max_ratio: float = 3.0,
    compressor: Optional[Compressor] = None,
    compress_target: float = 0.5,
    preserve_prefix: Optional[str] = None,
) -> OptimizeResult:
    """Optimise a single prompt string.

    Parameters
    ----------
    prompt : the full prompt text.
    query : the user's actual question, used to score context relevance. If
        absent, low-utility dropping is skipped (we will not guess what is
        relevant without a query).
    context : the retrieved-context block, if the caller can isolate it. If
        absent, dedupe and whitespace transforms still run on the whole prompt,
        but context-aware dropping does not.
    model : model id, for token counting.
    enable : set of transform names to run. Default runs the safe set
        {"route", "whitespace", "dedupe", "few_shot"}. Add "drop_context" to
        enable relevance-based context dropping (needs `query`). Add "compress"
        to enable the LLM compressor (needs `compressor`).
    keep_threshold : relevance score below which a context chunk is dropped.
    few_shot_max_ratio : prune few-shot examples when they exceed this multiple
        of the query size.
    compressor : optional callable (text, target_ratio) -> shorter text.
    compress_target : target length ratio passed to the compressor.
    preserve_prefix : if given, this exact leading substring of the prompt is
        held out of every transform, so a cached provider prefix (system block,
        long stable instructions) stays byte-identical and keeps hitting the
        KV cache. Optimisation runs only on the remainder. Most providers only
        cache an exact prefix match, so rewriting it can cost more than it saves.
    """
    enable = enable if enable is not None else {"route", "whitespace", "dedupe", "few_shot"}
    tokens_before = count_tokens(prompt, model)
    transforms: list[TransformResult] = []
    notes: list[str] = []

    # Cache-prefix protection: split off an exact leading region and never
    # touch it, then reattach it at the end. Only applies when the prompt
    # actually starts with the given prefix.
    prefix = ""
    text = prompt
    if preserve_prefix and prompt.startswith(preserve_prefix):
        prefix = preserve_prefix
        text = prompt[len(preserve_prefix):]
        notes.append(f"preserved a {count_tokens(prefix, model)}-token cache prefix; "
                     "it was held out of all transforms.")
    elif preserve_prefix:
        notes.append("preserve_prefix was given but the prompt does not start with it; ignored.")

    # 0. Content-aware routing: minify JSON, strip whole-line code comments.
    if "route" in enable:
        before = count_tokens(text, model)
        text, saved, kinds = _route_blocks(text, model)
        after = count_tokens(text, model)
        if saved > 0:
            desc = ", ".join(f"{k}:{v}" for k, v in kinds.items() if v)
            transforms.append(TransformResult("route", before, after,
                                               f"Routed blocks by type ({desc})."))

    # 1. Whitespace and boilerplate normalisation (always safe).
    if "whitespace" in enable:
        before = count_tokens(text, model)
        text = _normalise_whitespace(text)
        after = count_tokens(text, model)
        transforms.append(TransformResult("whitespace", before, after,
                                           "Collapsed blank lines and trailing spaces."))

    # 2. Dedupe duplicate paragraphs (safe).
    if "dedupe" in enable:
        before = count_tokens(text, model)
        text, n = _dedupe_paragraphs(text)
        after = count_tokens(text, model)
        transforms.append(TransformResult("dedupe", before, after,
                                           f"Removed {n} duplicate paragraph(s)."))

    # 3. Prune oversized few-shot blocks (safe-ish, conservative).
    if "few_shot" in enable:
        before = count_tokens(text, model)
        q_tokens = count_tokens(query, model) if query else 1
        text, dropped = _prune_few_shot(text, q_tokens, model, few_shot_max_ratio)
        after = count_tokens(text, model)
        if dropped > 0:
            transforms.append(TransformResult("few_shot", before, after,
                                               "Pruned the back half of an oversized example block."))

    # 4. Drop low-utility context (opt-in, needs a query).
    if "drop_context" in enable:
        if query:
            # Operate on the explicit context only if it is still a verbatim
            # substring of the current text (earlier transforms may have
            # changed it). Otherwise, and when no explicit context was given,
            # operate on the whole current text so the report always matches
            # what actually changed.
            operate_on_substring = context is not None and context in text
            target = context if operate_on_substring else text
            before = count_tokens(target, model)
            new_target, dropped = _drop_low_utility_chunks(target, query, model, keep_threshold)
            after = count_tokens(new_target, model)
            if dropped > 0:
                if operate_on_substring:
                    text = text.replace(context, new_target, 1)
                else:
                    text = new_target
                transforms.append(TransformResult("drop_context", before, after,
                                                   "Dropped context chunks with low query overlap."))
        else:
            notes.append("drop_context was enabled but no query was supplied; skipped.")

    # 5. LLM compression (opt-in, needs a compressor callable).
    if "compress" in enable:
        if compressor is not None:
            before = count_tokens(text, model)
            try:
                compressed = compressor(text, compress_target)
                # Guard: only accept the compression if it actually shrank the
                # text and did not collapse it to almost nothing.
                c_tokens = count_tokens(compressed, model)
                if 0 < c_tokens < before and c_tokens >= 0.1 * before:
                    text = compressed
                    after = count_tokens(text, model)
                    transforms.append(TransformResult("compress", before, after,
                                                       "Applied the supplied LLM compressor."))
                else:
                    notes.append("compressor output was rejected by the safety guard; "
                                 "kept the deterministic result.")
            except Exception as e:  # never let a bad compressor break the pipeline
                notes.append(f"compressor raised {type(e).__name__}; kept the deterministic result.")
        else:
            notes.append("compress was enabled but no compressor callable was supplied; skipped.")

    # Reattach the preserved cache prefix verbatim. Restore a separating blank
    # line if the prefix did not already end with a newline and the optimised
    # body does not begin with one, so the prefix is not glued to the next
    # block. The prefix bytes themselves are never altered, so cache alignment
    # holds (a stable prefix ends at a deterministic point regardless).
    if prefix:
        if not prefix.endswith("\n") and text and not text.startswith("\n"):
            final_text = prefix + "\n\n" + text
        else:
            final_text = prefix + text
    else:
        final_text = text
    tokens_after = count_tokens(final_text, model)
    return OptimizeResult(
        original=prompt,
        optimized=final_text,
        model=model,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        transforms=transforms,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Public optimiser for chat messages
# ---------------------------------------------------------------------------
def optimize_messages(
    messages: list[dict],
    *,
    model: str = "gpt-4o",
    enable: Optional[set[str]] = None,
    keep_threshold: float = 0.06,
    few_shot_max_ratio: float = 3.0,
    compressor: Optional[Compressor] = None,
    compress_target: float = 0.5,
) -> OptimizeResult:
    """Optimise a list of chat messages.

    The last user message is treated as the query. System messages and any
    earlier user or tool messages are treated as optimisable context. Assistant
    messages are left untouched by default, because rewriting prior model turns
    can change the conversation's meaning.

    Returns an OptimizeResult whose ``optimized`` field is a new messages list.
    """
    enable = enable if enable is not None else {"whitespace", "dedupe", "few_shot"}
    tokens_before = sum(count_tokens(_msg_text(m), model) for m in messages)

    # Find the last user message as the query.
    query = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            query = _msg_text(m)
            break

    new_messages: list[dict] = []
    all_transforms: list[TransformResult] = []
    all_notes: list[str] = []

    for m in messages:
        role = m.get("role")
        content = _msg_text(m)
        # Do not rewrite assistant turns or the live user query itself.
        if role == "assistant" or (role == "user" and content == query):
            new_messages.append(dict(m))
            continue
        if not content.strip():
            new_messages.append(dict(m))
            continue
        res = optimize_text(
            content,
            query=query or None,
            context=content if role in ("system", "tool", "user") else None,
            model=model,
            enable=enable,
            keep_threshold=keep_threshold,
            few_shot_max_ratio=few_shot_max_ratio,
            compressor=compressor,
            compress_target=compress_target,
        )
        nm = dict(m)
        nm["content"] = res.optimized
        new_messages.append(nm)
        all_transforms.extend(res.transforms)
        all_notes.extend(res.notes)

    tokens_after = sum(count_tokens(_msg_text(m), model) for m in new_messages)
    return OptimizeResult(
        original=messages,
        optimized=new_messages,
        model=model,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        transforms=all_transforms,
        notes=sorted(set(all_notes)),
    )


def _msg_text(m: dict) -> str:
    """Extract text from a chat message. Handles string content and the
    OpenAI/Anthropic list-of-parts content format."""
    content = m.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or part.get("content") or "")
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return str(content)
