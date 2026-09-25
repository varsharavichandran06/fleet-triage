"""
Append only, per run audit trail.

Each step the agent takes writes one JSON line: what it did, what it saw,
and how long it took, so a run can be inspected after the fact.
"""

import json
import os
import time
from typing import Any, Dict, List

from app.config import RUNS_DIR


def _run_dir(run_id: str) -> str:
    path = os.path.join(RUNS_DIR, run_id)
    os.makedirs(path, exist_ok=True)
    return path


def _audit_path(run_id: str) -> str:
    return os.path.join(_run_dir(run_id), "audit.jsonl")


def log_event(run_id: str, step: str, data: Dict[str, Any]) -> None:
    entry = {
        "timestamp": time.time(),
        "run_id": run_id,
        "step": step,
        "data": data,
    }
    with open(_audit_path(run_id), "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_trail(run_id: str) -> List[Dict[str, Any]]:
    path = _audit_path(run_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
