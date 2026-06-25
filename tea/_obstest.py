"""Deep tests for the observability features: output-cost modelling, word-level
red/green diffs, versioned templates, and the HTML dashboard.

Run: python -m tea._obstest

Adversarial where it matters: identical versions (no new version), diffs on
empty/whole-replacement text, HTML escaping in diffs, dashboard with zero
records, and output-cost pricing math against the known GPT-4o rates.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import tea
from tea.tokens import cost_breakdown
from tea.difftool import diff_ops, diff_html, diff_ansi, diff_stats
from tea.templates import TemplateStore
from tea.dashboard import build_dashboard


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    return bool(cond)


def main() -> int:
    ok = True

    # ===================================================================
    # Output-cost modelling
    # ===================================================================

    # 1. GPT-4o rates: 1M in = $2.50, 1M out = $10.00.
    cb = cost_breakdown("gpt-4o", 1_000_000, 1_000_000)
    ok &= check("gpt-4o input cost = $2.50/M", abs(cb["input_cost"] - 2.50) < 1e-9)
    ok &= check("gpt-4o output cost = $10.00/M", abs(cb["output_cost"] - 10.00) < 1e-9)
    ok &= check("total = input + output", abs(cb["total_cost"] - 12.50) < 1e-9)

    # 2. Per-token figures from the brainstorm: $0.0000025 in, $0.00001 out.
    cb1 = cost_breakdown("gpt-4o", 1, 1)
    ok &= check("one input token = $0.0000025", abs(cb1["input_cost"] - 0.0000025) < 1e-12)
    ok &= check("one output token = $0.00001", abs(cb1["output_cost"] - 0.00001) < 1e-12)

    # 3. Output defaults to zero when not provided.
    cb0 = cost_breakdown("gpt-4o", 500)
    ok &= check("no output tokens -> zero output cost", cb0["output_cost"] == 0.0)

    # 4. Unknown model falls back to the default rates without crashing.
    cbx = cost_breakdown("some-unknown-model", 1000, 1000)
    ok &= check("unknown model priced via fallback", cbx["total_cost"] > 0)

    # ===================================================================
    # Diff
    # ===================================================================

    # 5. Pure addition: only green, no red.
    ops = list(diff_ops("the cat", "the big cat"))
    ok &= check("addition yields an insert", any(t == "insert" for t, _ in ops))
    ok &= check("addition yields no delete", not any(t == "delete" for t, _ in ops))

    # 6. Pure removal: only red.
    ops = list(diff_ops("the big cat", "the cat"))
    ok &= check("removal yields a delete", any(t == "delete" for t, _ in ops))
    ok &= check("removal yields no insert", not any(t == "insert" for t, _ in ops))

    # 7. Replacement yields both a delete and an insert.
    ops = list(diff_ops("the cat sat", "the dog sat"))
    ok &= check("replacement yields delete and insert",
                any(t == "delete" for t, _ in ops) and any(t == "insert" for t, _ in ops))

    # 8. diff_stats counts words, not whitespace.
    st = diff_stats("alpha beta gamma", "alpha delta gamma epsilon")
    ok &= check("diff_stats removed beta", st["words_removed"] == 1)
    ok &= check("diff_stats added delta+epsilon", st["words_added"] == 2)

    # 9. HTML diff escapes markup so injected tags cannot break the page.
    h = diff_html("safe", "<script>alert(1)</script> safe")
    ok &= check("diff_html escapes angle brackets", "<script>" not in h and "&lt;script&gt;" in h)
    ok &= check("diff_html marks insert with ins class", "class='ins'" in h)

    # 10. Empty-to-text and text-to-empty do not crash and classify correctly.
    ok &= check("empty -> text is all insert",
                all(t in ("insert",) for t, _ in diff_ops("", "brand new content")))
    ok &= check("text -> empty is all delete",
                all(t in ("delete",) for t, _ in diff_ops("old content here", "")))

    # 11. Identical text: everything equal, nothing coloured.
    ok &= check("identical text is all equal",
                all(t == "equal" for t, _ in diff_ops("same words here", "same words here")))

    # 12. ANSI renderer returns a string and contains the reset code when changed.
    a = diff_ansi("a b c", "a x c")
    ok &= check("ansi diff returns string with reset", "\033[0m" in a)

    # 12a. Large diffs are fast (perf cap): a 5k-word diff must not hang.
    import time as _t
    big_old = "word " * 5000
    big_new = "word " * 4000 + "extra " * 100
    _t0 = _t.time()
    h = diff_html(big_old, big_new)
    elapsed = _t.time() - _t0
    ok &= check("5k-word diff completes under 1s", elapsed < 1.0)
    ok &= check("capped diff still shows change", "class='ins'" in h and "class='del'" in h)
    ok &= check("large identical diff is all-equal",
                all(t == "equal" for t, _ in diff_ops("w " * 5000, "w " * 5000)))

    # ===================================================================
    # Versioned templates
    # ===================================================================

    tmp = Path(tempfile.mkdtemp(prefix="tea_obs_"))
    try:
        store = TemplateStore(str(tmp))

        # 13. First record creates v1.
        e1 = store.record_version("bot", "You are a helpful assistant. Answer briefly.")
        ok &= check("first version is v1", e1["version"] == 1)

        # 14. Same text again does NOT create a new version.
        e1b = store.record_version("bot", "You are a helpful assistant. Answer briefly.")
        ok &= check("identical text keeps v1", e1b["version"] == 1)
        ok &= check("history has one version", len(store.history("bot")) == 1)

        # 15. Changed text creates v2 with a diff from v1.
        e2 = store.record_version("bot", "You are a concise assistant. Answer briefly and clearly.")
        ok &= check("changed text is v2", e2["version"] == 2)
        ok &= check("v2 records added words", e2["diff_from_prev"]["words_added"] >= 1)
        ok &= check("v2 records removed words", e2["diff_from_prev"]["words_removed"] >= 1)
        ok &= check("v2 has an html diff", "class='ins'" in e2["diff_html"] or "class='del'" in e2["diff_html"])

        # 16. Separate ids are independent.
        store.record_version("other", "different template entirely")
        ok &= check("separate id starts at v1", store.history("other")[0]["version"] == 1)
        ok &= check("all_ids lists both", set(store.all_ids()) >= {"bot", "other"})

        # 17. History persists across store instances (same dir).
        store2 = TemplateStore(str(tmp))
        ok &= check("history persists across instances", len(store2.history("bot")) == 2)

        # 18. Versioning through the public API + logger.
        d = tmp / "viapi"
        tea.optimize("alpha. alpha.\n\nbody one here.", query="body",
                     model="gpt-4o", enable={"dedupe"}, log=str(d), template_id="flow")
        tea.optimize("alpha.\n\nbody two different here now.", query="body",
                     model="gpt-4o", enable={"dedupe"}, log=str(d), template_id="flow")
        hist = TemplateStore(str(d)).history("flow")
        ok &= check("public api versioned the template", len(hist) >= 1)

        # 18a. A control call must NOT create a template version.
        d_ctrl = tmp / "ctrlver"
        for _ in range(6):
            tea.optimize("dup line here today. dup line here today.\n\ntail.",
                         query="dup", model="gpt-4o", enable={"dedupe"},
                         log=str(d_ctrl), template_id="cv", holdout=1.0)  # all control
        ok &= check("control calls create no template versions",
                    len(TemplateStore(str(d_ctrl)).history("cv")) == 0)

        # ===================================================================
        # Output cost in the log record
        # ===================================================================

        d2 = tmp / "cost"
        rec_dir_logger = tea.enable_logging(str(d2))
        tea.optimize("dup. dup.\n\nsome unique words here please.", query="dup",
                     model="gpt-4o", enable={"dedupe"}, output_tokens=300)
        tea.disable_logging()
        rec = json.loads((d2 / "tea_prompts.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
        ok &= check("record has output_tokens measured", rec["output_kind"] == "measured"
                    and rec["output_tokens"] == 300)
        ok &= check("record has cost breakdown", "cost" in rec and rec["cost"]["output"] > 0)
        ok &= check("output cost = 300 * $10/M", abs(rec["cost"]["output"] - 300 * 10.0 / 1e6) < 1e-9)

        d3 = tmp / "nocost"
        lg = tea.enable_logging(str(d3))
        tea.optimize("dup. dup.\n\nunique words here.", query="dup",
                     model="gpt-4o", enable={"dedupe"})
        tea.disable_logging()
        rec = json.loads((d3 / "tea_prompts.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
        ok &= check("no output -> output_kind estimated", rec["output_kind"] == "estimated")

        # ===================================================================
        # Dashboard
        # ===================================================================

        # 19. Build a dashboard from a real log; file exists and is self-contained.
        d4 = tmp / "dash"
        lg = tea.enable_logging(str(d4))
        for i in range(6):
            # A genuinely duplicated, dedupe-eligible sentence (>=2 content
            # words) so the optimised text really differs and the dashboard
            # has a red/green diff to render.
            dup_sentence = f"Report number {i} covers the quarterly results."
            tea.optimize(f"{dup_sentence} {dup_sentence}\n\nUnique tail body {i} here.",
                         query=f"report {i}", model="gpt-4o", enable={"dedupe"},
                         output_tokens=100 + i)
        tea.disable_logging()
        out = build_dashboard(str(d4))
        page = Path(out).read_text(encoding="utf-8")
        ok &= check("dashboard file written", Path(out).exists())
        ok &= check("dashboard is self-contained (no external src)",
                    "http://" not in page.split("tea_prompts.jsonl")[0] and "<svg" in page)
        ok &= check("dashboard has the prompt history table", "Prompt history" in page)
        ok &= check("dashboard shows red/green legend", "green = added" in page and "red = removed" in page)
        ok &= check("dashboard embeds a diff", "class='ins'" in page or "class='del'" in page)

        # 20. Dashboard on an empty log: no crash, shows the empty state.
        d5 = tmp / "empty"
        d5.mkdir()
        (d5 / "tea_prompts.jsonl").write_text("", encoding="utf-8")
        out = build_dashboard(str(d5))
        ok &= check("empty-log dashboard builds", Path(out).exists())
        ok &= check("empty-log dashboard says no records",
                    "No records" in Path(out).read_text(encoding="utf-8"))

        # 21. Missing log raises FileNotFoundError (caller handles it).
        try:
            build_dashboard(str(tmp / "does_not_exist"))
            ok &= check("missing log raises", False)
        except FileNotFoundError:
            ok &= check("missing log raises FileNotFoundError", True)

    finally:
        tea.disable_logging()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("ALL OBSERVABILITY TESTS PASS" if ok else "SOME OBSERVABILITY TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
