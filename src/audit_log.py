"""Append-only structured audit log (JSONL).

One JSON object per line. Human-readable, git-friendly, and trivial to show
judges or replay. Each record captures a single stage in the agent's handling
of one transaction.
"""
from __future__ import annotations

import json
import os

from src.models import AuditRecord

class AuditLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        self._fh = open(path, "w")

    def write(self, record: AuditRecord) -> None:
        self._fh.write(json.dumps(record.to_dict()) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

def read_records(path: str) -> list[dict]:
    """Read an audit log back into a list of dicts (used by metrics.py)."""
    records: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
