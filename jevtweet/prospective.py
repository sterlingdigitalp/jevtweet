"""Prospective prediction logging and fixed-window collection helpers (offline prep)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_HOURS = 48.0
WINDOW_TOLERANCE_HOURS = 1.0


def append_prediction(log_path: str | Path, entry: dict) -> dict:
    """Append a hash-chained prediction record; returns the stored entry."""
    log_path = Path(log_path)
    lines = log_path.read_text().splitlines() if log_path.exists() else []
    if lines:
        prev_hash = hashlib.sha256(lines[-1].encode()).hexdigest()
        if entry.get("target", {}).get("post_id") in [
            json.loads(line)["target"]["post_id"] for line in lines
        ]:
            raise ValueError("Target already logged; predictions are write-once")
    else:
        prev_hash = "genesis"
    stored = {
        "prediction_id": f"pred-{len(lines) + 1:05d}",
        "prev_hash": prev_hash,
        "logged_at": datetime.now().astimezone().isoformat(),
        **entry,
    }
    for key in ("method", "target", "evidence_ids", "estimate"):
        if key not in stored:
            raise ValueError(f"Missing prediction field: {key}")
    with log_path.open("a") as handle:
        handle.write(json.dumps(stored) + "\n")
    return stored


def verify_log(log_path: str | Path) -> dict:
    """Verify hash chain continuity and write-once targets."""
    lines = Path(log_path).read_text().splitlines()
    seen, prev_hash = set(), "genesis"
    for line in lines:
        entry = json.loads(line)
        if entry["prev_hash"] != prev_hash or entry["target"]["post_id"] in seen:
            return {"valid": False, "entries": len(lines)}
        seen.add(entry["target"]["post_id"])
        prev_hash = hashlib.sha256(line.encode()).hexdigest()
    return {"valid": True, "entries": len(lines)}


def collection_due(log_path: str | Path, now: datetime) -> list[dict]:
    """Predictions whose 48h observation window has closed (collection due)."""
    due = []
    for line in Path(log_path).read_text().splitlines():
        entry = json.loads(line)
        published = datetime.fromisoformat(entry["target"]["claimed_published_at"])
        if entry.get("observed_at") is None and published + timedelta(hours=WINDOW_HOURS) <= now:
            due.append(entry)
    return due


def verify_observation_window(published_at: datetime, observed_at: datetime) -> bool:
    """Fixed 48h window within the strict ±1h tolerance (mirrors outcome rules)."""
    elapsed = (observed_at - published_at).total_seconds() / 3600
    return abs(elapsed - WINDOW_HOURS) <= WINDOW_TOLERANCE_HOURS
