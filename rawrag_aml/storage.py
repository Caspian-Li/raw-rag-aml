"""Atomic user snapshots and idempotent Add receipts, using SQLite WAL."""
from array import array
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import sqlite3
import sys


class Conflict(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def encode_vector(vector):
    values = array("d", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def decode_vector(blob):
    values = array("d")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return list(values)


class Store:
    def __init__(self, path, fingerprint):
        self.path = path
        self.fingerprint = fingerprint
        self.lock_file = None

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = open(str(self.path) + ".lock", "a+")
        os.chmod(str(self.path) + ".lock", 0o600)
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            raise RuntimeError("Database already in use; run exactly one Uvicorn worker") from None
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL, turn_context TEXT NOT NULL,
                    last_access TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, timestamp INTEGER, received_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, seq)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL, chunk_json TEXT NOT NULL,
                    embedding BLOB NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    digest TEXT NOT NULL, response TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS users_expiry ON users(last_access);
            """)
            db.execute("BEGIN IMMEDIATE")
            try:
                metadata = {"profile": self.fingerprint, "schema": "1", "vectors": "float64-le"}
                for key, value in metadata.items():
                    prior = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                    if prior and prior[0] != value:
                        raise RuntimeError("Stored data profile differs; use a fresh database for this version")
                    db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, value))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        os.chmod(self.path, 0o600)

    def close(self):
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def receipt(self, request_id, digest):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT user_id,digest,response FROM requests WHERE request_id=?", (request_id,)).fetchone()
                if row is None:
                    db.commit()
                    return None
                if row["digest"] != digest:
                    raise Conflict("request_id already committed with a different payload")
                db.execute("UPDATE users SET last_access=? WHERE user_id=?", (utcnow(), row["user_id"]))
                db.commit()
                return json.loads(row["response"])
            except BaseException:
                db.rollback()
                raise

    def snapshot(self, user_id, include_messages=False):
        with self.connect() as db:
            db.execute("UPDATE users SET last_access=? WHERE user_id=?", (utcnow(), user_id))
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                db.commit()
                return {"revision": 0, "messages": [], "index": None, "created_at": {}}
            chunks = db.execute("SELECT * FROM chunks WHERE user_id=? ORDER BY chunk_index", (user_id,)).fetchall()
            messages = [dict(item) for item in db.execute("SELECT * FROM messages WHERE user_id=? ORDER BY seq", (user_id,))] if include_messages else []
            db.commit()
        return {
            "revision": row["revision"], "messages": messages,
            "index": {"conversation_id": row["conversation_id"],
                      "chunks": [json.loads(item["chunk_json"]) for item in chunks],
                      "embeddings": [decode_vector(item["embedding"]) for item in chunks],
                      "turn_context": json.loads(row["turn_context"])},
            "created_at": {item["chunk_index"]: item["created_at"] for item in chunks},
        }

    def commit_add(self, payload, digest, expected_revision, built):
        now = utcnow()
        response = {"success": True, "request_id": payload.request_id,
                    "user_id": payload.user_id, "session_id": payload.session_id}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                prior = db.execute("SELECT digest,response FROM requests WHERE request_id=?", (payload.request_id,)).fetchone()
                if prior:
                    if prior["digest"] != digest:
                        raise Conflict("request_id already committed with a different payload")
                    db.execute("UPDATE users SET last_access=? WHERE user_id=?", (now, payload.user_id))
                    db.commit()
                    return json.loads(prior["response"])
                user = db.execute("SELECT revision FROM users WHERE user_id=?", (payload.user_id,)).fetchone()
                revision = user[0] if user else 0
                if revision != expected_revision:
                    raise Conflict("User snapshot changed; retry the same Add request")
                db.execute("""INSERT INTO users VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                           revision=excluded.revision, conversation_id=excluded.conversation_id,
                           turn_context=excluded.turn_context, last_access=excluded.last_access""",
                           (payload.user_id, revision + 1, built["conversation_id"], json.dumps(built["turn_context"]), now))
                seq = db.execute("SELECT COALESCE(MAX(seq)+1,0) FROM messages WHERE user_id=?", (payload.user_id,)).fetchone()[0]
                db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", [
                    (payload.user_id, seq+i, payload.session_id, m.role, m.content, m.timestamp, now)
                    for i, m in enumerate(payload.messages)
                ])
                previous = {r["chunk_index"]: r["chunk_json"] for r in db.execute("SELECT chunk_index,chunk_json FROM chunks WHERE user_id=?", (payload.user_id,))}
                for chunk, vector in zip(built["chunks"], built["embeddings"]):
                    index = chunk["chunk_index"]
                    serialized = json.dumps(chunk, ensure_ascii=False, sort_keys=True)
                    if previous.get(index) == serialized:
                        continue
                    db.execute("""INSERT INTO chunks VALUES (?,?,?,?,?) ON CONFLICT(user_id,chunk_index)
                               DO UPDATE SET chunk_json=excluded.chunk_json, embedding=excluded.embedding,
                               created_at=excluded.created_at""",
                               (payload.user_id, index, serialized, encode_vector(vector), now))
                db.execute("DELETE FROM chunks WHERE user_id=? AND chunk_index>=?", (payload.user_id, len(built["chunks"])))
                db.execute("INSERT INTO requests VALUES (?,?,?,?)", (payload.request_id, payload.user_id, digest, json.dumps(response)))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return response

    def expire(self, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("DELETE FROM users WHERE last_access < ?", (cutoff,)).rowcount
            db.commit()
            # Overwrite deleted SQLite content and truncate WAL where possible.
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return count
