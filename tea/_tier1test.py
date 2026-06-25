"""Deep edge-case tests for the tier-1 features: content routing,
cache-prefix preservation, and measurement integrity (holdout + CIs).

Run: python -m tea._tier1test

These cases are intentionally adversarial: malformed JSON, JSON that contains
comment-like strings, code fences with no language, nested fences, prefixes
that are the whole prompt, holdout at 0 and 1, single-call confidence
intervals, and control-group accounting. The goal is to break the new code,
not to confirm the happy path.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import tea
from tea.optimizer import classify_block, optimize_text, _minify_json, _strip_code_comments
from tea.tokens import count_tokens


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    return bool(cond)


def main() -> int:
    ok = True

    # ===================================================================
    # Content routing
    # ===================================================================

    # 1. Valid JSON minifies and preserves key order.
    j = '{\n  "z": 1,\n  "a": 2,\n  "m": [1, 2, 3]\n}'
    mini, changed = _minify_json(j)
    ok &= check("json minified and changed", changed and " " not in mini.replace('": ', '"'))
    ok &= check("json key order preserved (z before a)", mini.index('"z"') < mini.index('"a"'))
    ok &= check("json round-trips to same object", json.loads(mini) == json.loads(j))

    # 2. Malformed JSON is left untouched (no crash, no change).
    bad = '{"a": 1, "b": }'
    out, changed = _minify_json(bad)
    ok &= check("malformed json untouched", out == bad and not changed)

    # 3. JSON string values containing braces/quotes survive.
    tricky = '{"msg": "he said {hi} and \\"bye\\"", "n": 5}'
    out, changed = _minify_json(tricky)
    ok &= check("json with tricky string values round-trips",
                json.loads(out) == json.loads(tricky))

    # 4. classify_block does not call a prose paragraph JSON or code.
    ok &= check("prose stays prose", classify_block("The quick brown fox jumped over it.") == "prose")
    ok &= check("a number is not json-routed destructively",
                classify_block("42") in ("prose", "json"))

    # 5. Code fence with no language: comments stripped only if unambiguous.
    code_nolang = "```\n# comment\nx = 1\n```"
    out, changed = _strip_code_comments(code_nolang)
    ok &= check("no-lang fence: comment handling does not crash", isinstance(out, str))
    ok &= check("no-lang fence: code line kept", "x = 1" in out)

    # 6. Python fence: whole-line comments stripped, code kept, fence intact.
    py = "```python\n# header\nimport os\n# trailing\nprint(os.getcwd())\n```"
    out, changed = _strip_code_comments(py)
    ok &= check("python comments stripped", "# header" not in out and "# trailing" not in out)
    ok &= check("python code kept", "import os" in out and "print(os.getcwd())" in out)
    ok &= check("python fence intact", out.count("```") == 2)

    # 7. A '#' inside a string on a code line is NOT treated as a comment
    #    (we only strip whole-line comments, so this must stay).
    py2 = '```python\nurl = "http://x#frag"\n```'
    out, changed = _strip_code_comments(py2)
    ok &= check("inline hash in string preserved", '#frag' in out and not changed)

    # 8. JS fence uses // comments.
    js = "```js\n// a comment\nconst x = 1;\n```"
    out, changed = _strip_code_comments(js)
    ok &= check("js // comment stripped", "// a comment" not in out and "const x = 1;" in out)

    # 9. Routing a mixed document: prose + json block + code block.
    mixed = (
        "Here is the config.\n\n"
        '{\n  "debug": true,\n  "level": 3\n}\n\n'
        "And the snippet:\n\n"
        "```python\n# setup\nrun()\n```"
    )
    r = optimize_text(mixed, model="gpt-4o", enable={"route"})
    ok &= check("mixed doc: tokens drop", r.tokens_after < r.tokens_before)
    ok &= check("mixed doc: prose retained", "Here is the config" in r.optimized)
    ok &= check("mixed doc: json minified", '"debug":true' in r.optimized or '"debug": true' not in r.optimized)
    ok &= check("mixed doc: code fence survived", r.optimized.count("```") == 2)
    ok &= check("mixed doc: code still runs run()", "run()" in r.optimized)

    # 10. Routing never corrupts a fence that contains a blank line.
    blanky = "```python\ndef f():\n\n    return 1\n```"
    r = optimize_text(blanky, model="gpt-4o", enable={"route", "whitespace"})
    ok &= check("blank line inside code survives routing", "return 1" in r.optimized
                and r.optimized.count("```") == 2)

    # 11. Report tokens_after matches the real optimised text after routing.
    ok &= check("route report matches optimised text",
                r.tokens_after == count_tokens(r.optimized, "gpt-4o"))

    # 11a. Top-level JSON array minifies and round-trips.
    arr = "[1, 2, 3, 4, 5]"
    out, changed = _minify_json(arr)
    ok &= check("json array minifies", out == "[1,2,3,4,5]" and changed)

    # 11b. Unicode in JSON survives minification.
    uni = '{"name": "café", "emoji": "\U0001F600"}'
    out, changed = _minify_json(uni)
    ok &= check("unicode json preserved", json.loads(out) == json.loads(uni))

    # 11c. A code block whose lines are ALL comments is kept, not emptied.
    allc = "```python\n# one\n# two\n# three\n```"
    out, changed = _strip_code_comments(allc)
    ok &= check("all-comment block not emptied", out == allc and not changed)

    # 11d. Large JSON minifies, stays valid, and reports a sane reduction.
    big = json.dumps({"k" + str(i): list(range(5)) for i in range(500)}, indent=2)
    r = optimize_text(big, model="gpt-4o", enable={"route"})
    ok &= check("large json still valid after routing", bool(json.loads(r.optimized)))
    ok &= check("large json reduction is sane (0-100)", 0 < r.reduction_pct < 100)

    # 11e. JSON-looking prose with an opening brace but invalid is left as prose.
    notjson = "{this is not json, just prose with a brace"
    ok &= check("brace-prefixed prose is not json", classify_block(notjson) == "prose")

    # ===================================================================
    # Cache-prefix preservation
    # ===================================================================

    prefix = "SYSTEM: stable cached preamble that must stay byte for byte."

    # 12. Prefix preserved exactly; only the tail is optimised.
    body = "\n\nfact. fact.\n\nunrelated weather note about rain."
    r = optimize_text(prefix + body, model="gpt-4o", query="fact",
                      enable={"dedupe", "drop_context"}, keep_threshold=0.1,
                      preserve_prefix=prefix)
    ok &= check("prefix preserved byte-for-byte", r.optimized.startswith(prefix))
    ok &= check("prefix preservation noted", any("cache prefix" in n for n in r.notes))

    # 13. Prefix that is the ENTIRE prompt: nothing to optimise, no crash.
    r = optimize_text(prefix, model="gpt-4o", enable={"dedupe"}, preserve_prefix=prefix)
    ok &= check("whole-prompt prefix: returned intact", r.optimized == prefix)
    ok &= check("whole-prompt prefix: no negative savings", r.tokens_saved >= 0)

    # 14. preserve_prefix that the prompt does NOT start with: ignored cleanly.
    r = optimize_text("totally different text here. here.", model="gpt-4o",
                      enable={"dedupe"}, preserve_prefix="NONMATCHING PREFIX")
    ok &= check("non-matching prefix ignored", any("does not start with" in n for n in r.notes))

    # 15. A duplicate that spans the prefix boundary is NOT merged across it.
    #     The prefix copy must remain even if the body repeats it.
    dupacross = prefix + "\n\n" + "Repeated body line here today. Repeated body line here today."
    r = optimize_text(dupacross, model="gpt-4o", enable={"dedupe"}, preserve_prefix=prefix)
    ok &= check("prefix intact when body repeats", r.optimized.startswith(prefix))

    # 16. Prefix preservation through the public optimize() on messages is not
    #     applicable (messages have no single prefix), so just confirm str path.
    rr = tea.optimize(prefix + "\n\na. a.", model="gpt-4o", enable={"dedupe"},
                      preserve_prefix=prefix)
    ok &= check("public optimize passes preserve_prefix through",
                rr.optimized.startswith(prefix))

    # 16a. preserve_prefix longer than the whole prompt: ignored, no crash.
    r = optimize_text("short", model="gpt-4o", enable={"dedupe"},
                      preserve_prefix="a much longer prefix than the prompt itself")
    ok &= check("over-long prefix handled", r.optimized == "short")

    # 16b. Prefix is not glued to the body: a separator survives even after
    #      whitespace/dedupe normalise the body's leading blank line away.
    glue_prefix = "SYSTEM: end no newline"
    r = optimize_text(glue_prefix + "\n\nbody fact. body fact.\n\nmore unique words.",
                      model="gpt-4o", query="fact", enable={"route", "whitespace", "dedupe"},
                      preserve_prefix=glue_prefix)
    ok &= check("prefix not glued to body",
                not r.optimized.startswith(glue_prefix + "body"))

    # ===================================================================
    # Measurement integrity: holdout + CIs
    # ===================================================================

    tmp = Path(tempfile.mkdtemp(prefix="tea_t1_"))
    try:
        # 17. holdout=1.0 -> every call is control, unchanged, tagged control.
        d = tmp / "allcontrol"
        for _ in range(5):
            r = tea.optimize("dup. dup.\n\nmore text here that is unique.",
                             query="dup", model="gpt-4o",
                             enable=tea.AGGRESSIVE_TRANSFORMS, log=str(d), holdout=1.0)
        led = tea.logbook.TEALogger(str(d)).ledger
        ok &= check("holdout=1.0: all calls are control", led["control_calls"] == 5)
        ok &= check("holdout=1.0: no optimised calls", led["optimised_calls"] == 0)
        ok &= check("holdout=1.0: savings_kind measured", led["savings_kind"] == "measured")
        # control calls are unchanged
        last = json.loads((d / "tea_prompts.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
        ok &= check("control record tokens unchanged", last["tokens_before"] == last["tokens_after"])
        ok &= check("control source tag", last["source"].endswith(":control"))

        # 18. holdout=0.0 -> never control; savings_kind estimated.
        d = tmp / "nocontrol"
        for _ in range(5):
            tea.optimize("dup. dup.\n\nunique tail content here please.",
                         query="dup", model="gpt-4o",
                         enable=tea.AGGRESSIVE_TRANSFORMS, log=str(d), holdout=0.0)
        led = tea.logbook.TEALogger(str(d)).ledger
        ok &= check("holdout=0: no control calls", led["control_calls"] == 0)
        ok &= check("holdout=0: savings_kind estimated", led["savings_kind"] == "estimated")

        # 19. CI present and ordered lo <= mean <= hi for many calls.
        d = tmp / "ci"
        lg = tea.enable_logging(str(d))
        for i in range(40):
            tea.optimize(f"Line {i}. Line {i}.\n\nUnique body {i} with several words here.",
                         query=f"body {i}", model="gpt-4o", enable={"dedupe"})
        led = lg.ledger
        lo, hi = led["reduction_ci95"]
        mean = led["mean_reduction_pct"]
        ok &= check("CI ordered lo <= mean <= hi", lo <= mean <= hi)
        ok &= check("CI within [0,100]", 0.0 <= lo and hi <= 100.0)
        ok &= check("optimised_calls counted", led["optimised_calls"] == 40)
        tea.disable_logging()

        # 20. Single optimised call: CI collapses to the mean, no crash.
        d = tmp / "single"
        lg = tea.enable_logging(str(d))
        tea.optimize("alpha. alpha.\n\nbeta gamma delta epsilon words.",
                     query="alpha", model="gpt-4o", enable={"dedupe"})
        led = lg.ledger
        lo, hi = led["reduction_ci95"]
        ok &= check("single call: CI collapses to mean", lo == led["mean_reduction_pct"] == hi)
        tea.disable_logging()

        # 21. Ledger CI survives a reload (persistence of raw accumulators).
        reopened = tea.logbook.TEALogger(str(d))
        ok &= check("CI stats persist across reload",
                    reopened.ledger["optimised_calls"] == 1
                    and reopened.ledger["mean_reduction_pct"] == led["mean_reduction_pct"])

        # 21a. Out-of-range holdout values clamp sensibly.
        d = tmp / "oob"
        r_neg = tea.optimize("a. a.\n\nbody words here now.", query="a", model="gpt-4o",
                             enable={"dedupe"}, log=str(d), holdout=-0.5)
        ok &= check("holdout < 0 treated as no holdout (optimised)",
                    r_neg.tokens_after <= r_neg.tokens_before
                    and not any("control" in n for n in r_neg.notes))
        r_big = tea.optimize("a. a.\n\nbody words here now.", query="a", model="gpt-4o",
                             enable={"dedupe"}, log=str(d), holdout=5.0)
        ok &= check("holdout > 1 treated as full holdout (control)",
                    any("control" in n for n in r_big.notes))

        # 22. Mixed holdout: both buckets accounted, totals add up.
        d = tmp / "mixed"
        lg = tea.enable_logging(str(d))
        for i in range(60):
            tea.optimize(f"x{i}. x{i}.\n\nbody {i} with content words here now.",
                         query=f"body {i}", model="gpt-4o",
                         enable={"dedupe"}, holdout=0.5)
        led = lg.ledger
        ok &= check("mixed holdout: buckets sum to calls",
                    led["optimised_calls"] + led["control_calls"] == led["calls"] == 60)
        ok &= check("mixed holdout: some of each (statistically near 50/50)",
                    10 <= led["control_calls"] <= 50)
        tea.disable_logging()

    finally:
        tea.disable_logging()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("ALL TIER-1 TESTS PASS" if ok else "SOME TIER-1 TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
