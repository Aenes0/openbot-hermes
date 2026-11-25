"""Sinais para o Curador: o que o uso real mostra que merece virar proposta. O julgamento fica no agente Curador."""
from __future__ import annotations

import re
import sqlite3
import unicodedata

from . import scan
from .pending import Pending
from .sessions import Sessions, SessionsError, open_readonly
from .skills import SkillStore
from .tools import HermesToolbox, ToolError

MAX_PROPOSALS_PER_RUN = 5
MAX_ITEMS = 20
SNIPPET = 300
REPEAT_MIN_THREADS = 3
REPEAT_WORDS = 8
REPEAT_MIN_CHARS = 30
CORRECTION_RE = re.compile(
    r"^\s*(n[ãa]o\s*[,.!:;]|errad[oa]\b|incorret[oa]\b|n[ãa]o\s+(era|[ée])\s+isso\b)", re.I)


def _safe(text: str | None, limit: int = SNIPPET) -> str:
    """Corta, tira segredos e neutraliza texto com cara de injeção: o Curador recebe só dados limpos."""
    text = scan.redact_secrets((text or "").strip())[:limit]
    reason = scan.check_text(text)
    return f"[trecho omitido pela varredura: {reason}]" if reason else text


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


class Curator:
    def __init__(self, sessions: Sessions, pending: Pending, skills: SkillStore):
        self.sessions = sessions
        self.pending = pending
        self.skills = skills

    def signals(self, since: str) -> dict:
        """Correções do Alexandre, execuções com erro seguidas de sucesso e pedidos repetidos desde `since` (ISO)."""
        if not isinstance(since, str):
            raise ValueError("since deve ser uma data ISO, por exemplo 2026-11-01T00:00:00Z")
        since = since.strip()
        self.sessions.sync()
        messages = self.sessions.thread_messages(since)
        corrections = self._corrections(messages, since)
        recovered = self._error_then_success(since)
        repeated = self._repeated(messages, since)
        return {"since": since, "empty": not (corrections or recovered or repeated),
                "user_corrections": corrections, "error_then_success": recovered, "repeated_requests": repeated,
                "skills": [item["name"] for item in self.skills.list()], "pending_count": len(self.pending.list()),
                "max_proposals": MAX_PROPOSALS_PER_RUN}

    @staticmethod
    def _corrections(messages: list[dict], since: str) -> list[dict]:
        found, previous = [], None
        for message in messages:
            if previous is not None and previous["thread_id"] != message["thread_id"]:
                previous = None
            if (message["author"] == "user" and message["created_at"] >= since and previous is not None
                    and previous["author"] == "assistant" and CORRECTION_RE.match(message["text"])):
                found.append({"thread_id": message["thread_id"], "message_id": message["message_id"],
                              "agent_id": message["agent_id"], "created_at": message["created_at"],
                              "user_text": _safe(message["text"]), "previous_assistant": _safe(previous["text"])})
            previous = message
        return found[:MAX_ITEMS]

    def _error_then_success(self, since: str) -> list[dict]:
        try:
            conn = open_readonly(self.sessions.openbot_db)
            try:
                rows = conn.execute(
                    "SELECT run_id, routine_id, agent_id, status, error, created_at FROM projection_routine_runs "
                    "WHERE status IN ('failed', 'succeeded') AND created_at >= ? ORDER BY routine_id, created_at",
                    (since,)).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as err:
            raise Sessions._explain(err) from None
        found, last_failure = [], None
        for run_id, routine_id, agent_id, status, error, created_at in rows:
            if last_failure is not None and last_failure["routine_id"] != routine_id:
                last_failure = None
            if status == "failed":
                last_failure = {"run_id": run_id, "routine_id": routine_id, "agent_id": agent_id,
                                "error": _safe(error), "created_at": created_at}
            elif last_failure is not None:
                found.append({"routine_id": routine_id, "agent_id": agent_id, "failed_run_id": last_failure["run_id"],
                              "failed_at": last_failure["created_at"], "error": last_failure["error"],
                              "succeeded_run_id": run_id, "succeeded_at": created_at})
                last_failure = None
        return found[:MAX_ITEMS]

    @staticmethod
    def _repeated(messages: list[dict], since: str) -> list[dict]:
        groups: dict[str, dict] = {}
        for message in messages:
            if message["author"] != "user" or message["created_at"] < since or len(message["text"]) < REPEAT_MIN_CHARS:
                continue
            words = re.findall(r"\w+", _normalize(message["text"]))[:REPEAT_WORDS]
            if len(words) < REPEAT_WORDS:
                continue
            group = groups.setdefault(" ".join(words), {"threads": set(), "agents": set()})
            group["threads"].add(message["thread_id"])
            group["agents"].add(message["agent_id"] or "")
        found = [{"pattern": _safe(pattern), "count": len(group["threads"]), "threads": sorted(group["threads"])[:5],
                  "agent_ids": sorted(group["agents"])}
                 for pattern, group in groups.items() if len(group["threads"]) >= REPEAT_MIN_THREADS]
        found.sort(key=lambda item: -item["count"])
        return found[:MAX_ITEMS]

    def register(self, toolbox: HermesToolbox) -> None:
        """Expõe curator_signals(since) como ferramenta MCP (só leitura)."""
        def handler(args: dict) -> dict:
            try:
                return self.signals(args.get("since") or "")
            except ValueError as err:
                raise ToolError(str(err)) from None
            except SessionsError as err:
                raise ToolError(str(err)) from None

        toolbox.register(
            "curator_signals",
            "Sinais de uso real desde uma data (ISO): correções do Alexandre, rotinas que falharam e se recuperaram, "
            "pedidos repetidos. Só leitura; o Curador transforma isso em propostas (no máximo 5 por rodada).",
            {"type": "object", "properties": {"since": {"type": "string", "description": "Ex.: 2026-11-24T00:00:00Z"}},
             "required": []},
            handler)
