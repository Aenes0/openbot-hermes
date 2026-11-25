"""Banco falso do OpenBot: mesmas tabelas e colunas relevantes do banco real (nunca toca o real)."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE projection_threads (
    thread_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    active_turn_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_event_sequence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE projection_thread_messages (
    thread_id TEXT NOT NULL REFERENCES projection_threads(thread_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL, turn_id TEXT, author TEXT NOT NULL, status TEXT NOT NULL, item_type TEXT,
    created_at TEXT NOT NULL, ordinal INTEGER NOT NULL, message_json TEXT NOT NULL CHECK(json_valid(message_json)),
    last_event_sequence INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(thread_id, message_id));
CREATE TABLE projection_routine_runs (
    run_id TEXT PRIMARY KEY, routine_id TEXT NOT NULL, agent_id TEXT NOT NULL, status TEXT NOT NULL,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '', routine_name TEXT NOT NULL DEFAULT '');
CREATE TABLE agent_usage_records (
    agent_id TEXT NOT NULL, record_id TEXT NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, occurred_at TEXT NOT NULL,
    tokens_json TEXT NOT NULL CHECK(json_valid(tokens_json)), estimated_cost_usd REAL,
    rate_basis TEXT, recorded_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(agent_id, record_id));
"""


class FakeOpenBot:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self._ordinal = 0
        self.db.commit()

    def thread(self, thread_id: str, agent_id: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO projection_threads(thread_id, agent_id, created_at, updated_at) "
                        "VALUES (?,?,?,?)", (thread_id, agent_id, "2026-11-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z"))
        self.db.commit()

    def message(self, thread_id, message_id, author, text, item_type=None, created_at="2026-11-01T10:00:00.000Z",
                turn_id=None, status="completed"):
        self._ordinal += 1
        self.db.execute(
            "INSERT INTO projection_thread_messages(thread_id, message_id, turn_id, author, status, item_type, "
            "created_at, ordinal, message_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (thread_id, message_id, turn_id, author, status, item_type, created_at, self._ordinal,
             json.dumps({"text": text}, ensure_ascii=False)))
        self.db.commit()

    def usage(self, agent_id, record_id, session_id, turn_id, cost, model="sonnet", occurred_at="2026-11-01T10:00:00.000Z"):
        self.db.execute(
            "INSERT INTO agent_usage_records(agent_id, record_id, session_id, turn_id, provider, model, occurred_at, "
            "tokens_json, estimated_cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (agent_id, record_id, session_id, turn_id, "anthropic", model, occurred_at,
             json.dumps({"uncachedInput": 10, "output": 5}), cost))
        self.db.commit()

    def routine_run(self, run_id, status, agent_id="agent-1", routine_id="r1", created_at="2026-11-01T10:00:00.000Z",
                    error=None):
        self.db.execute("INSERT INTO projection_routine_runs(run_id, routine_id, agent_id, status, error, created_at) "
                        "VALUES (?,?,?,?,?,?)", (run_id, routine_id, agent_id, status, error, created_at))
        self.db.commit()

    def close(self):
        self.db.close()
