# Changelog

All notable changes to the Token Efficiency Agent. This project follows
semantic versioning.

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
