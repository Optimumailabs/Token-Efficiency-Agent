# Changelog

All notable changes to the Token Efficiency Agent. This project follows
semantic versioning.

## 0.3.0

### Added

- **Content-aware routing** (`route`, on by default): each block is classified
  as JSON, code, or prose and gets the right compressor. JSON is minified with
  key order preserved; whole-line code comments are stripped by language;
  prose falls through to the safe transforms. Never empties a code block.
- **Cache-prefix preservation** (`preserve_prefix=`): hold a stable leading
  region byte-for-byte so provider KV caches keep hitting, optimising only the
  tail. Handles whole-prompt, over-long, and non-matching prefixes gracefully,
  and keeps a separator so the prefix is not glued to the body.
- **Measurement integrity**: `holdout=` leaves a fraction of calls unoptimised
  as a control group (tagged `:control`); the ledger reports
  `savings_kind` as `measured` once a control exists or `estimated` otherwise,
  plus a mean per-call reduction and a 95% confidence interval
  (`reduction_ci95`) that persists across reloads.
- Deep tier-1 edge-case suite (`tea/_tier1test.py`), 52 checks.

### Fixed

- Token-count fallback (no tiktoken) undercounted whitespace-poor content like
  minified JSON, overstating savings. Now uses `max(words * 1.3, chars / 4)`.
- Stripping comments from an all-comment code block no longer empties it.

## 0.2.0

### Added

- Per-prompt logging (`tea/logbook.py`): JSONL records, a human-readable log,
  and a cumulative savings ledger. Each record carries the original and
  optimised prompt, tokens before and after, tokens saved, reduction percent,
  dollars saved, the transforms that fired, process memory (RSS plus
  tracemalloc peak), and a `source` tag.
- `tea.enable_logging()`, `tea.disable_logging()`, the `TEA_LOG_DIR` env var,
  and a per-call `log=` argument on `tea.optimize()` and every adapter.
- Pip-installable packaging (`pyproject.toml`) with zero required dependencies
  and optional extras `[tokenizer]`, `[memory]`, `[all]`.
- Console scripts `tea-optimize` and `tea-score` (`tea/cli.py`).
- Claude Code plugin manifests (`.claude-plugin/`) and the skill moved to
  `skills/token-efficiency-agent/SKILL.md`.
- VS Code extension (`vscode-extension/`), Marketplace-ready.
- GitHub Actions workflow that builds, tests, attaches the wheel to a release,
  and can publish to PyPI and the VS Code Marketplace.
- Expanded edge-case suite (long lines, code-only prompts, non-string message
  content, nested message parts, mixed language, markdown tables, concurrency)
  and a dedicated logging test suite.
- Community docs: `CONTRIBUTING.md`, `SECURITY.md`, and a deeper `docs/` set.

### Changed

- Adapters route through the public `optimize()` so logging and source tagging
  apply uniformly.

## 0.1.0

### Added

- Initial release: deterministic prompt optimiser (whitespace, dedupe,
  few-shot pruning, capped context dropping) plus an optional LLM compressor
  hook.
- Adapters for OpenAI, Anthropic, LangChain, CrewAI, and AutoGen.
- `score.py` and `optimize.py` CLIs.
- Self-test and edge-case suites.
- MIT license.
