# Logging

TEA can append a structured record for every optimise call. This is how a team
audits what TEA did, proves the savings, and watches cost over time.

## Turning it on

Three ways, in order of precedence:

```python
# 1. Per call
tea.optimize(prompt, query="...", log=True)            # default dir
tea.optimize(prompt, query="...", log="/path/to/dir")  # one-off dir
tea.optimize(prompt, query="...", log=False)           # never, even if a default is set

# 2. A default logger for the process
import tea
tea.enable_logging("tea_logs")
tea.optimize(prompt, query="...")                      # logged automatically

# 3. Environment variable, no code change
#    export TEA_LOG_DIR=/var/log/tea
```

Logging is off by default.

## Files written

| File | Contents |
|---|---|
| `tea_prompts.jsonl` | One JSON object per call. Append-only. Parse this for analysis. |
| `tea_prompts.log` | The same records formatted for humans to read. |
| `tea_ledger.json` | Running totals across all calls, rewritten each time. |

## Record fields

| Field | Meaning |
|---|---|
| `ts` | UTC timestamp, millisecond precision |
| `source` | Where the call came from: `api`, `openai`, `anthropic`, `langchain`, `crewai`, `autogen`, `cli` |
| `model` | Model id used for token counting |
| `tokens_before`, `tokens_after`, `tokens_saved` | Token counts |
| `reduction_pct` | Percent reduction |
| `usd_saved` | Estimated dollars saved on input tokens |
| `transforms` | Each transform that ran, with its own saving and a note |
| `notes` | Anything the optimiser wants to flag (skipped transforms, guard rejections) |
| `query` | The query used for relevance scoring, if any |
| `memory.rss_bytes` | Resident set size of the process at the call (null if unavailable) |
| `memory.peak_kib` | tracemalloc peak for the call |
| `ledger` | Cumulative totals at the time of this record |
| `original_prompt`, `optimized_prompt` | Full text of both |

## The ledger

`tea_ledger.json` carries the running totals and survives across runs: a new
logger pointed at the same directory continues the counts.

```json
{
  "calls": 128,
  "tokens_before": 740000,
  "tokens_after": 410000,
  "tokens_saved": 330000,
  "reduction_pct": 44.59,
  "usd_saved": 0.825
}
```

## Memory stats

`memory.rss_bytes` uses `psutil` if installed, then the Unix `resource` module,
and is `null` on Windows without `psutil`. `memory.peak_kib` is the
`tracemalloc` peak measured around the optimise call. Install the `[memory]`
extra for reliable RSS on all platforms.

## Privacy

By default the full prompt text is written to the log. If your prompts contain
secrets or personal data, treat the log directory as sensitive. The shipped
`.gitignore` excludes `tea_logs/` and the log files. See `SECURITY.md`.

## Guarantees

- Logging never raises into your call path. A logging failure is swallowed and
  the optimisation result is returned regardless.
- Writes are guarded by a lock, so concurrent calls do not interleave lines in
  the JSONL file. The test suite verifies this with 80 concurrent writes.
