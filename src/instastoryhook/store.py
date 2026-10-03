"""Single SQLite transaction stores each event and its delivery state."""

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"Another worker holds {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                created REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS delivery_due ON events(state, next_attempt);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY, payload TEXT NOT NULL, refreshed REAL NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def contains(self, event_id):
        return (
            self.db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone() is not None
        )

    def add(self, event, now):
        # An event row is also its outbox row, so there is no two-write crash window.
        body = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        with self.db:
            row = self.db.execute(
                "INSERT OR IGNORE INTO events(id,payload,created) VALUES (?,?,?)",
                (event["id"], body, now),
            )
        return row.rowcount == 1

    def account(self, username, now):
        row = self.db.execute(
            "SELECT payload FROM accounts WHERE username=? AND refreshed>?",
            (username, now - 86400),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_account(self, username, account, now):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO accounts VALUES (?,?,?)",
                (
                    username,
                    json.dumps(account),
                    now,
                ),
            )

    def bind_webhook(self, url):
        row = self.db.execute("SELECT value FROM metadata WHERE key='webhook'").fetchone()
        if row and row[0] != url:
            raise ValueError("This database is bound to a different webhook; use a new database")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('webhook',?)", (url,))

    def due(self, now, limit=50):
        return self.db.execute(
            "SELECT * FROM events WHERE state='pending' AND next_attempt<=? ORDER BY seq LIMIT ?",
            (now, limit),
        ).fetchall()

    def delivered(self, event_id):
        with self.db:
            self.db.execute(
                "UPDATE events SET state='delivered',attempts=attempts+1,last_error=NULL WHERE id=?",
                (event_id,),
            )

    def failed(self, row, now, error, max_attempts, retry_after=0):
        attempts = row["attempts"] + 1
        delay = max(min(3600, 5 * 2 ** min(attempts, 10)), min(86400, retry_after))
        with self.db:
            self.db.execute(
                "UPDATE events SET state=?, attempts=?,next_attempt=?,last_error=? WHERE id=?",
                (
                    "dead" if attempts >= max_attempts else "pending",
                    attempts,
                    now + delay,
                    error,
                    row["id"],
                ),
            )

    def export(self, after=0):
        for row in self.db.execute(
            "SELECT seq,payload FROM events WHERE seq>? ORDER BY seq", (after,)
        ):
            yield {"cursor": row["seq"], "event": json.loads(row["payload"])}

    def replay(self, event_id=None):
        with self.db:
            if event_id:
                return self.db.execute(
                    "UPDATE events SET state='pending',attempts=0,next_attempt=0,last_error=NULL WHERE id=?",
                    (event_id,),
                ).rowcount
            return self.db.execute(
                "UPDATE events SET state='pending',attempts=0,next_attempt=0,last_error=NULL WHERE state='dead'"
            ).rowcount

    def status(self):
        counts = dict(self.db.execute("SELECT state,count(*) FROM events GROUP BY state"))
        health = self.db.execute("SELECT value FROM metadata WHERE key='health'").fetchone()
        return {"events": counts, "health": json.loads(health[0]) if health else None}

    def health(self, payload):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('health',?)", (json.dumps(payload),)
            )
