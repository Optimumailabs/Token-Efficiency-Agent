"""Versioned prompt templates.

When a caller passes a ``template_id``, TEA keeps an ordered version history for
that id. Each optimise call that produces a different optimised prompt appends a
new version (v1, v2, v3, ...) together with the word-level diff from the
previous version. This is the "one maintained template, iterated over time"
view: you can see how a prompt evolved and what each round added or removed.

History is stored as JSON under the log directory, in ``templates/<id>.json``,
so it survives across runs. If no logger or log directory is active, the
history is kept in memory for the process only.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .difftool import diff_stats, diff_html
from .tokens import count_tokens


def _safe_id(template_id: str) -> str:
    """Make a template id safe to use as a filename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", template_id)[:128] or "template"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TemplateStore:
    """Per-id version history, optionally persisted to ``base_dir/templates``."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base = Path(base_dir) / "templates" if base_dir else None
        if self.base:
            self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._mem: dict[str, dict] = {}

    def _path(self, template_id: str) -> Optional[Path]:
        return (self.base / f"{_safe_id(template_id)}.json") if self.base else None

    def _load(self, template_id: str) -> dict:
        p = self._path(template_id)
        if p and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._mem.get(template_id, {"template_id": template_id, "versions": []})

    def _save(self, template_id: str, data: dict) -> None:
        self._mem[template_id] = data
        p = self._path(template_id)
        if p:
            try:
                p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def record_version(self, template_id: str, text: str, model: str = "gpt-4o") -> dict:
        """Append a new version if the text differs from the latest. Returns the
        version entry (new or the unchanged latest). Each entry carries the
        version number, timestamp, the text, its token count, and the diff from
        the previous version (added/removed word counts plus an HTML diff)."""
        with self._lock:
            data = self._load(template_id)
            versions = data["versions"]
            prev_text = versions[-1]["text"] if versions else ""
            if versions and prev_text == text:
                return versions[-1]  # no change, no new version
            entry = {
                "version": len(versions) + 1,
                "ts": _utc_now_iso(),
                "text": text,
                "tokens": count_tokens(text, model),
                "diff_from_prev": diff_stats(prev_text, text) if versions else
                                  {"words_added": len([w for w in re.findall(r"\S+", text)]),
                                   "words_removed": 0},
                "diff_html": diff_html(prev_text, text) if versions else diff_html("", text),
            }
            versions.append(entry)
            self._save(template_id, data)
            return entry

    def history(self, template_id: str) -> list[dict]:
        with self._lock:
            return list(self._load(template_id)["versions"])

    def all_ids(self) -> list[str]:
        ids = set(self._mem.keys())
        if self.base and self.base.exists():
            for p in self.base.glob("*.json"):
                ids.add(p.stem)
        return sorted(ids)
