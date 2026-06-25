"""Static HTML observability dashboard.

Reads a TEA JSONL log and writes a single self-contained HTML file: no server,
no JavaScript framework, no external assets, no dependencies. Charts are inline
SVG drawn from the log data. The page shows:

- headline cards: total calls, input tokens before/after, tokens saved, dollars
  saved, optimised vs control split;
- input vs output tokens over time;
- cumulative dollars saved over time;
- a prompt-history table where each row expands to a red/green word diff of the
  original vs optimised prompt.

Build it with ``tea-dashboard --log tea_logs --out report.html`` or
``tea.build_dashboard("tea_logs", "report.html")``.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from .difftool import diff_html


def _read_records(log_dir: str) -> list[dict]:
    path = Path(log_dir) / "tea_prompts.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no log at {path}")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _svg_line(series: list[float], width=720, height=140, color="#2b6cb0",
              label="") -> str:
    """A minimal inline-SVG line chart for one numeric series."""
    if not series:
        return f"<svg width='{width}' height='{height}'></svg>"
    n = len(series)
    lo, hi = min(series), max(series)
    rng = (hi - lo) or 1.0
    pad = 8
    def x(i): return pad + (i * (width - 2 * pad) / max(1, n - 1))
    def y(v): return height - pad - ((v - lo) * (height - 2 * pad) / rng)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series))
    return (
        f"<svg width='{width}' height='{height}' role='img' aria-label='{html.escape(label)}'>"
        f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{pts}'/>"
        f"</svg>"
    )


def _bars(pairs: list[tuple[str, float]], width=720, height=160) -> str:
    """A simple two-tone bar comparison (before vs after style)."""
    if not pairs:
        return ""
    maxv = max(v for _, v in pairs) or 1.0
    bar_h = 22
    rows = []
    y = 6
    for name, v in pairs:
        w = (v / maxv) * (width - 160)
        rows.append(
            f"<text x='0' y='{y+16}' font-size='12' fill='#cbd5e1'>{html.escape(name)}</text>"
            f"<rect x='150' y='{y}' width='{w:.1f}' height='{bar_h}' rx='3' fill='#48c9b0'/>"
            f"<text x='{155+w:.1f}' y='{y+16}' font-size='12' fill='#cbd5e1'>{v:,.0f}</text>"
        )
        y += bar_h + 8
    return f"<svg width='{width}' height='{y+6}'>{''.join(rows)}</svg>"


def build_dashboard(log_dir: str, out_file: Optional[str] = None) -> str:
    """Build the HTML dashboard from ``log_dir`` and write it to ``out_file``
    (default ``<log_dir>/tea_dashboard.html``). Returns the output path."""
    records = _read_records(log_dir)
    out_file = out_file or str(Path(log_dir) / "tea_dashboard.html")

    calls = len(records)
    optimised = [r for r in records if not str(r.get("source", "")).endswith(":control")]
    control = [r for r in records if str(r.get("source", "")).endswith(":control")]
    tok_before = sum(r.get("tokens_before", 0) for r in records)
    tok_after = sum(r.get("tokens_after", 0) for r in records)
    tok_saved = sum(r.get("tokens_saved", 0) for r in records)
    usd_saved = sum(r.get("usd_saved", 0.0) for r in records)
    out_total = sum((r.get("output_tokens") or 0) for r in records)
    reduction = (100.0 * tok_saved / tok_before) if tok_before else 0.0

    # Latest ledger snapshot, if present, carries the CI.
    ledger = records[-1].get("ledger", {}) if records else {}

    # Series for charts.
    in_series = [float(r.get("tokens_before", 0)) for r in records]
    out_series = [float(r.get("output_tokens") or 0) for r in records]
    cum = []
    run = 0.0
    for r in records:
        run += r.get("usd_saved", 0.0)
        cum.append(run)

    # Prompt-history rows with expandable diffs.
    rows = []
    for i, r in enumerate(records[-200:], 1):  # cap the table for huge logs
        orig = r.get("original_prompt") or r.get("original_preview") or ""
        opt = r.get("optimized_prompt") or r.get("optimized_preview") or ""
        d = diff_html(orig, opt)
        tmpl = r.get("template") or {}
        tlabel = (f"{tmpl.get('template_id')} v{tmpl.get('version')}"
                  if tmpl else "")
        rows.append(
            "<tr class='row' onclick='this.nextElementSibling.classList.toggle(\"open\")'>"
            f"<td>{i}</td><td>{html.escape(str(r.get('source','')))}</td>"
            f"<td>{r.get('tokens_before',0):,}</td><td>{r.get('tokens_after',0):,}</td>"
            f"<td>{r.get('reduction_pct',0)}%</td>"
            f"<td>${r.get('usd_saved',0.0):.5f}</td>"
            f"<td>{html.escape(tlabel)}</td></tr>"
            f"<tr class='diff'><td colspan='7'><div class='diffbox'>{d}</div></td></tr>"
        )

    if calls == 0:
        rows.append("<tr><td colspan='7'>No records in the log yet.</td></tr>")

    def card(label, value):
        return (f"<div class='card'><div class='cv'>{value}</div>"
                f"<div class='cl'>{html.escape(label)}</div></div>")

    cards = "".join([
        card("Calls", f"{calls:,}"),
        card("Optimised / Control", f"{len(optimised):,} / {len(control):,}"),
        card("Input tokens before", f"{tok_before:,}"),
        card("Input tokens after", f"{tok_after:,}"),
        card("Tokens saved", f"{tok_saved:,}"),
        card("Input reduction", f"{reduction:.1f}%"),
        card("Output tokens (sum)", f"{out_total:,}"),
        card("Input $ saved", f"${usd_saved:.4f}"),
    ])

    ci = ledger.get("reduction_ci95")
    ci_line = ""
    if ci:
        ci_line = (f"<p class='sub'>Mean per-call reduction "
                   f"{ledger.get('mean_reduction_pct','?')}% "
                   f"(95% CI {ci[0]}%..{ci[1]}%), savings "
                   f"<b>{ledger.get('savings_kind','estimated')}</b>.</p>")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TEA dashboard</title>
<style>
  body {{ background:#0f1626; color:#e2e8f0; font-family:Inter,system-ui,Segoe UI,Arial,sans-serif; margin:0; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 2px; }}
  .sub {{ color:#94a3b8; margin:2px 0 18px; font-size:13px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:24px; }}
  .card {{ background:#16203a; border:1px solid #243352; border-radius:10px; padding:14px; }}
  .cv {{ font-size:22px; font-weight:700; color:#48c9b0; }}
  .cl {{ font-size:12px; color:#94a3b8; margin-top:4px; }}
  .panel {{ background:#16203a; border:1px solid #243352; border-radius:10px; padding:16px; margin-bottom:20px; }}
  .panel h2 {{ font-size:14px; margin:0 0 10px; color:#cbd5e1; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #243352; }}
  th {{ color:#94a3b8; font-weight:600; }}
  .row {{ cursor:pointer; }}
  .row:hover {{ background:#1b2742; }}
  .diff {{ display:none; }}
  .diff.open {{ display:table-row; }}
  .diffbox {{ white-space:pre-wrap; word-break:break-word; background:#0c1322; border-radius:8px; padding:12px; font-family:ui-monospace,Consolas,monospace; font-size:12px; line-height:1.5; }}
  .eq {{ color:#cbd5e1; }}
  .del {{ color:#f87171; text-decoration:line-through; }}
  .ins {{ color:#4ade80; }}
  .legend span {{ margin-right:16px; font-size:12px; }}
</style></head><body>
<h1>Token Efficiency Agent - observability</h1>
<p class="sub">Generated from {html.escape(str(Path(log_dir) / 'tea_prompts.jsonl'))}. {calls:,} record(s).</p>
{ci_line}
<div class="cards">{cards}</div>

<div class="panel"><h2>Input tokens per call</h2>{_svg_line(in_series, color="#2b6cb0", label="input tokens per call")}</div>
<div class="panel"><h2>Output tokens per call</h2>{_svg_line(out_series, color="#d69e2e", label="output tokens per call")}</div>
<div class="panel"><h2>Cumulative input dollars saved</h2>{_svg_line(cum, color="#48c9b0", label="cumulative dollars saved")}</div>

<div class="panel">
  <h2>Prompt history</h2>
  <p class="legend"><span style="color:#4ade80">green = added</span><span style="color:#f87171">red = removed</span><span style="color:#94a3b8">click a row to expand the diff</span></p>
  <table>
    <thead><tr><th>#</th><th>source</th><th>in (before)</th><th>in (after)</th><th>reduction</th><th>$ saved</th><th>template</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</div>
</body></html>"""

    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text(page, encoding="utf-8")
    return out_file
