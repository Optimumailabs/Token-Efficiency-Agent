#!/usr/bin/env python3
"""Token Efficiency Agent, dashboard CLI.

Builds the self-contained HTML observability dashboard from a TEA log. This is
the no-install entry point: it depends only on the bundled ``tea`` package one
directory up, so a fresh clone or fork can run it without ``pip install``.

Usage:
    python scripts/dashboard.py --log tea_logs --out report.html
    python scripts/dashboard.py --log tea_logs            # writes <log>/tea_dashboard.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the bundled tea package importable when run from anywhere.
_HERE = Path(__file__).resolve()
_SKILL_ROOT = _HERE.parent.parent           # .../product/skill (repo root in a fork)
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

import tea  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="TEA dashboard: build a self-contained HTML report from a log.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--log", default="tea_logs", help="Log directory to read.")
    p.add_argument("--out", default=None,
                   help="Output HTML file. Defaults to <log>/tea_dashboard.html.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        out = tea.build_dashboard(args.log, args.out)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    print(json.dumps({"dashboard": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
