"""Private SQLite persistence. JSON records have immutable IDs unless explicitly updated."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .contracts import canonical, now

class ConflictError(ValueError):
    pass

class BudgetError(ValueError):
    pass

class Store:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "jevtweet.sqlite3"
        with self.connect() as db:
            db.executescript((Path(__file__).parent / "migrations" / "001_initial.sql").read_text())
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def put(self, kind: str, record_id: str, value: Any, *, replace: bool = False) -> dict:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        body = canonical(value)
        with self.transaction() as db:
            old = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind,record_id)).fetchone()
            if old and old[0] != body and not replace:
                raise ConflictError(f"Conflicting immutable {kind} identity: {record_id}")
            db.execute("INSERT INTO records(kind,id,body,created_at) VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",(kind,record_id,body,now().isoformat()))
        return value

    def get(self, kind: str, record_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM records WHERE kind=? AND id=?",(kind,record_id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM records WHERE kind=? ORDER BY created_at,id",(kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def cached(self, input_hash: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT r.body FROM cache c JOIN records r ON r.kind='judgment' AND r.id=c.judgment_id WHERE c.input_hash=?",(input_hash,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, input_hash: str, judgment_id: str):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO cache VALUES(?,?)",(input_hash,judgment_id))

    def reserve(self, reservation_id: str, amount: float, ceiling: float, rpm: int):
        import math, time
        if not math.isfinite(amount) or amount < 0 or not math.isfinite(ceiling) or ceiling <= 0:
            raise BudgetError("Live execution requires an explicit positive spending ceiling")
        with self.transaction() as db:
            spent = db.execute("SELECT COALESCE(SUM(charged),0) FROM spending").fetchone()[0]
            if spent + amount > ceiling:
                raise BudgetError("Persistent account spending ceiling reached")
            recent = db.execute("SELECT COUNT(*) FROM spending WHERE created_epoch>?",(time.time()-60,)).fetchone()[0]
            if recent >= rpm:
                raise BudgetError("Account request rate limit reached; resume after one minute")
            db.execute("INSERT INTO spending VALUES(?,?,?,?)",(reservation_id,amount,amount,time.time()))

    def reconcile(self, reservation_id: str, actual: float | None):
        # Unknown network outcomes retain their full reservation, including crashes/cancellation.
        if actual is not None:
            with self.connect() as db:
                db.execute("UPDATE spending SET charged=? WHERE id=?",(actual,reservation_id))

    def spending(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT COALESCE(SUM(charged),0),COUNT(*) FROM spending").fetchone()
        return {"charged_or_reserved_usd":row[0],"reserved_attempts":row[1]}
