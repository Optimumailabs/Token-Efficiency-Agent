# Contributing to Token Efficiency Agent

Thanks for considering a contribution. TEA is small on purpose, so the bar for
changes is mostly about keeping it safe and well-tested.

## Ground rules

1. **Deterministic transforms must stay meaning-safe.** `whitespace`, `dedupe`,
   and `few_shot` may never change what the model is asked to do. Anything that
   could alter meaning (`drop_context`, `compress`) is opt-in and bounded.
2. **No new hard dependencies.** The core package installs with zero runtime
   dependencies. `tiktoken` and `psutil` stay optional. Framework SDKs are
   imported lazily inside their adapters, never at package import time.
3. **Every new transform or adapter needs a test.** Add it to the relevant
   suite under `tea/`.

## Development setup

```bash
git clone https://github.com/Optimumailabs/Token-Efficiency-Agent.git
cd Token-Efficiency-Agent
pip install -e ".[dev]"
```

## Run the tests

```bash
python -m tea._selftest      # core functional checks
python -m tea._edgetest      # edge cases: inputs, pipeline, concurrency
python -m tea._logtest       # logging checks
```

All three must pass before a pull request is merged.

## Adding a transform

1. Implement the transform in `tea/optimizer.py` and gate it behind a name in
   the `enable` set.
2. If it can change meaning, leave it out of `SAFE_TRANSFORMS` and document why.
3. Add cases to `tea/_selftest.py` and at least one edge case to
   `tea/_edgetest.py`.
4. Update the transforms table in `README.md` and the skill in
   `skills/token-efficiency-agent/SKILL.md`.

## Adding a framework adapter

1. Add a module under `tea/integrations/`.
2. Import the framework lazily, inside the function that needs it.
3. Route through `tea.optimize(...)` so logging and the `source` tag work for
   free, or call the logger directly if the adapter merges several results.
4. Add the adapter to the integrations table in `README.md`.

## Style

- Plain, direct prose in docs. No em dashes.
- Keep functions small and the public API stable.
- Match the surrounding code; do not reformat unrelated lines.

## Commits

Write clear commit messages describing what changed and why. One logical change
per commit where practical.
