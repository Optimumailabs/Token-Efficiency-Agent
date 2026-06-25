"""Token counting and cost estimation, shared across the TEA package.

Token counting uses tiktoken when available and falls back to a whitespace
approximation otherwise. The fallback is rough but keeps the package usable in
environments without tiktoken installed.
"""

from __future__ import annotations

import re
from functools import lru_cache


# Per-million-token rates, split into prefill (input) and decode (output).
# Update before any production billing claim. Values reflect public pricing
# at the time of writing and are intentionally easy to override per call.
PRICING_USD_PER_M = {
    "gpt-4o":            {"prefill":  2.50, "decode": 10.00},
    "gpt-4o-mini":       {"prefill":  0.15, "decode":  0.60},
    "gpt-4.1":           {"prefill":  2.00, "decode":  8.00},
    "claude-opus-4-8":   {"prefill": 15.00, "decode": 75.00},
    "claude-sonnet-4-6": {"prefill":  3.00, "decode": 15.00},
    "claude-haiku-4-5":  {"prefill":  1.00, "decode":  5.00},
    # Self-hosted approximation (vLLM / SGLang on an H100 at roughly $2/hr).
    "self-hosted":       {"prefill":  0.05, "decode":  0.20},
}

_DEFAULT_MODEL = "gpt-4o"


@lru_cache(maxsize=8)
def _encoding_for(model: str):
    """Return a tiktoken encoding, or None if tiktoken is unavailable."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except (KeyError, Exception):
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def count_tokens(text: str, model: str = _DEFAULT_MODEL) -> int:
    """Count tokens in `text` for `model`.

    Uses tiktoken if present. Otherwise falls back to an approximation that is
    the larger of two rules of thumb:

    - 1.3 tokens per whitespace word (good for English prose), and
    - 1 token per 4 characters (the standard byte-pair rule that still works
      for whitespace-poor content like minified JSON or code).

    Taking the max keeps the estimate honest on dense content. Without the
    chars/4 floor, minified JSON would read as a single "word" and the
    optimiser would massively overstate its savings. Anthropic models have no
    public tokenizer and also use this fallback, off by a few per cent, which
    is fine for the relative before/after comparison the optimiser relies on.
    """
    if not text:
        return 0
    enc = _encoding_for(model)
    if enc is not None:
        return len(enc.encode(text))
    words = len(re.findall(r"\S+", text))
    by_words = words * 1.3
    by_chars = len(text) / 4.0
    return max(1, int(round(max(by_words, by_chars))))


def tokenizer_is_exact(model: str) -> bool:
    """True when token counts come from a real tokenizer, not the fallback."""
    return _encoding_for(model) is not None


def estimate_cost(model: str, n_prompt: int, n_completion: int) -> float:
    """Estimated dollar cost for a single call in USD (input + output)."""
    rates = PRICING_USD_PER_M.get(model, PRICING_USD_PER_M[_DEFAULT_MODEL])
    return (n_prompt * rates["prefill"] + n_completion * rates["decode"]) / 1_000_000


def cost_breakdown(model: str, n_prompt: int, n_completion: int = 0) -> dict:
    """Split dollar cost into input and output, at the model's per-token rates.

    Output is the expensive side: for GPT-4o, input is $2.50 per million tokens
    and output is $10.00 per million. The optimiser shrinks the input prompt,
    so input_cost is what TEA directly reduces; output_cost is shown for the
    full picture and depends on how long the model's reply is."""
    rates = PRICING_USD_PER_M.get(model, PRICING_USD_PER_M[_DEFAULT_MODEL])
    input_cost = n_prompt * rates["prefill"] / 1_000_000
    output_cost = n_completion * rates["decode"] / 1_000_000
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
        "rate_in_per_m": rates["prefill"],
        "rate_out_per_m": rates["decode"],
    }
