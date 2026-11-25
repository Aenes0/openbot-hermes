"""Ledger: execuções por skill/tier/tipo de tarefa, qualidade (quem decide) e custo (lido do OpenBot, só leitura)."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .sessions import Sessions, open_readonly
from .skills import TIERS

OUTCOMES = ("ok", "fail")
# Precedência da qualidade: correção do Alexandre > teste > revisor barato > rotina.
SOURCES = {"routine": 1, "reviewer": 2, "test": 3, "correction": 4}
TERMINAL_ROUTINE = {"succeeded": "ok", "failed": "fail"}
EMPTY_STATS = {"runs": 0, "ok": 0, "fail": 0, "avg_cost_usd": 0.0}


class LedgerError(Exception):
    """Registro inválido; a mensagem diz o que corrigir."""


class Ledger:
    def __init__(self, home: Path, openbot_db: Path):
        self.home = Path(home)
        self.openbot_db = Path(openbot_db)
        self.path = self.home / "ledger.db"

    def _db(self) -> sqlite3.Connection:
        self.home.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        conn = sqlite3.connect(self.path, timeout=5.0)
        if fresh:
            os.chmod(self.path, 0o600)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, skill TEXT NOT NULL, "
            "task_type TEXT NOT NULL, tier TEXT NOT NULL, outcome TEXT NOT NULL, source TEXT NOT NULL, "
            "ref TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS usage(agent_id TEXT NOT NULL, record_id TEXT NOT NULL, session_id TEXT, "
            "turn_id TEXT NOT NULL, model TEXT, cost REAL, occurred_at TEXT, PRIMARY KEY(agent_id, record_id));"
            "CREATE INDEX IF NOT EXISTS usage_turn ON usage(turn_id);"
            "CREATE TABLE IF NOT EXISTS routine_runs(run_id TEXT PRIMARY KEY, routine_id TEXT, agent_id TEXT, "
            "status TEXT NOT NULL, created_at TEXT);"
            "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, skill TEXT NOT NULL, "
            "kind TEXT NOT NULL, message TEXT NOT NULL, after_run_id INTEGER NOT NULL, at TEXT NOT NULL);")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- entrada ----------
    def ingest(self) -> int:
        """Copia do OpenBot (somente leitura) o custo por turno e o status das rotinas. Idempotente."""
        try:
            source = open_readonly(self.openbot_db)
        except sqlite3.Error as err:
            raise Sessions._explain(err) from None
        changed = 0
        db = self._db()
        try:
            try:
                usage = source.execute(
                    "SELECT agent_id, record_id, session_id, turn_id, model, estimated_cost_usd, occurred_at "
                    "FROM agent_usage_records").fetchall()
                routines = source.execute(
                    "SELECT run_id, routine_id, agent_id, status, created_at FROM projection_routine_runs "
                    "WHERE status IN ('succeeded', 'failed')").fetchall()
            except sqlite3.Error as err:
                raise Sessions._explain(err) from None
            with db:
                for row in usage:
                    changed += db.execute("INSERT OR IGNORE INTO usage(agent_id, record_id, session_id, turn_id, model, "
                                          "cost, occurred_at) VALUES (?,?,?,?,?,?,?)", row).rowcount
                for row in routines:
                    changed += db.execute(
                        "INSERT INTO routine_runs(run_id, routine_id, agent_id, status, created_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status "
                        "WHERE routine_runs.status != excluded.status", row).rowcount
        finally:
            source.close()
            db.close()
        return changed

    def record(self, skill: str, task_type: str, tier: str, outcome: str, source: str, ref: str = "") -> int:
        """Registra uma execução. `ref` (turno ou rodada de rotina) junta registros da mesma execução e liga o custo."""
        if not isinstance(skill, str) or not skill.strip() or not isinstance(task_type, str) or not task_type.strip():
            raise LedgerError("skill e task_type são obrigatórios.")
        if tier not in TIERS:
            raise LedgerError(f"tier deve ser um de: {', '.join(TIERS)}.")
        if outcome not in OUTCOMES:
            raise LedgerError("outcome deve ser 'ok' ou 'fail'.")
        if source not in SOURCES:
            raise LedgerError(f"source deve ser um de: {', '.join(SOURCES)}.")
        db = self._db()
        try:
            with db:
                cursor = db.execute("INSERT INTO runs(skill, task_type, tier, outcome, source, ref, recorded_at) "
                                    "VALUES (?,?,?,?,?,?,?)", (skill.strip(), task_type.strip(), tier, outcome, source,
                                                               ref or "", self._now()))
            return cursor.lastrowid
        finally:
            db.close()

    # ---------- leitura ----------
    def _executions(self, skill: str | None = None, task_type: str | None = None, tier: str | None = None,
                    after_id: int = 0) -> list[dict]:
        """Uma linha por execução, com o resultado decidido pela precedência da qualidade."""
        db = self._db()
        try:
            where, params = ["id > ?"], [after_id]
            for column, value in (("skill", skill), ("task_type", task_type), ("tier", tier)):
                if value is not None:
                    where.append(f"{column} = ?")
                    params.append(value)
            rows = db.execute(f"SELECT id, skill, task_type, tier, outcome, source, ref FROM runs "
                              f"WHERE {' AND '.join(where)} ORDER BY id", params).fetchall()
            routine = dict(db.execute("SELECT run_id, status FROM routine_runs").fetchall())
            costs = dict(db.execute("SELECT turn_id, SUM(cost) FROM usage WHERE cost IS NOT NULL "
                                    "GROUP BY turn_id").fetchall())
        finally:
            db.close()
        chosen: dict[str, dict] = {}
        for run_id, run_skill, run_task, run_tier, outcome, source, ref in rows:
            if source == "routine" and ref in routine:
                outcome = TERMINAL_ROUTINE[routine[ref]]
            key = f"{run_skill}|{ref}" if ref else f"#{run_id}"
            item = {"id": run_id, "skill": run_skill, "task_type": run_task, "tier": run_tier, "outcome": outcome,
                    "rank": SOURCES[source], "cost": costs.get(ref) if ref else None}
            current = chosen.get(key)
            if current is None or item["rank"] >= current["rank"]:
                chosen[key] = item
        return list(chosen.values())

    @staticmethod
    def _summarize(executions: list[dict]) -> dict:
        if not executions:
            return dict(EMPTY_STATS)
        known = [e["cost"] for e in executions if e["cost"] is not None]
        ok = sum(1 for e in executions if e["outcome"] == "ok")
        return {"runs": len(executions), "ok": ok, "fail": len(executions) - ok,
                "avg_cost_usd": (sum(known) / len(known)) if known else 0.0}

    def stats(self, skill: str, tier: str, after_id: int = 0) -> dict:
        return self._summarize(self._executions(skill=skill, tier=tier, after_id=after_id))

    def tier_stats(self, task_type: str) -> dict:
        """Estatísticas por tier, somando todas as skills daquele tipo de tarefa."""
        by_tier: dict[str, list[dict]] = {}
        for item in self._executions(task_type=task_type):
            by_tier.setdefault(item["tier"], []).append(item)
        return {tier: self._summarize(items) for tier, items in by_tier.items()}

    # ---------- marcas e avisos ----------
    def _max_run_id(self, db: sqlite3.Connection) -> int:
        return db.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0]

    def mark_restored(self, skill: str, from_tier: str, to_tier: str, reason: str = "") -> int:
        """Registra o aviso de que o tier subiu sozinho e devolve a marca: só execuções depois dela contam."""
        message = f"Tier de {skill} voltou de {from_tier} para {to_tier}" + (f": {reason}" if reason else ".")
        db = self._db()
        try:
            with db:
                mark = self._max_run_id(db)
                db.execute("INSERT INTO events(skill, kind, message, after_run_id, at) VALUES (?,?,?,?,?)",
                           (skill, "restored", message, mark, self._now()))
            return mark
        finally:
            db.close()

    def last_mark(self, skill: str) -> int:
        db = self._db()
        try:
            row = db.execute("SELECT COALESCE(MAX(after_run_id), 0) FROM events WHERE skill = ? AND kind = 'restored'",
                             (skill,)).fetchone()
            return row[0]
        finally:
            db.close()

    def warnings(self, skill: str | None = None) -> list[dict]:
        db = self._db()
        try:
            sql, params = "SELECT skill, message, at FROM events", []
            if skill is not None:
                sql += " WHERE skill = ?"
                params.append(skill)
            rows = db.execute(sql + " ORDER BY id", params).fetchall()
        finally:
            db.close()
        return [{"skill": s, "message": m, "at": a} for s, m, a in rows]
