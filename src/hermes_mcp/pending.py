"""Fila de aprovação: toda escrita do agente vira proposta em pending/. Só a CLI aplica ou rejeita."""
from __future__ import annotations

import difflib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import scan
from .fsutil import atomic_write
from .memory import Memory
from .skills import SkillError, SkillStore

SKILL_ACTIONS = ("create", "patch", "delete", "write_file", "remove_file")
MEMORY_ACTIONS = ("add", "patch", "remove")
SCANNED_KEYS = ("body", "new", "content", "description", "text")


class PendingError(Exception):
    """Proposta inválida ou que não pôde ser aplicada; a mensagem diz por quê."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Pending:
    def __init__(self, home: Path, skills: SkillStore, memory: Memory):
        self.home = Path(home)
        self.skills = skills
        self.memory = memory

    # ---------- arquivos ----------
    def _folder(self, state: str) -> Path:
        return self.home / state

    def _path(self, state: str, pid: str) -> Path:
        if not isinstance(pid, str) or not pid or "/" in pid or "\\" in pid or pid.startswith("."):
            raise PendingError(f"Id de proposta inválido: {pid!r}")
        return self._folder(state) / f"{pid}.json"

    def _write(self, state: str, record: dict) -> None:
        atomic_write(self._path(state, record["id"]), json.dumps(record, ensure_ascii=False, indent=2))

    def _load(self, pid: str) -> dict:
        try:
            return json.loads(self._path("pending", pid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PendingError(f"Proposta pendente não encontrada: {pid}") from None

    def _move(self, record: dict, state: str) -> None:
        self._write(state, record)
        self._path("pending", record["id"]).unlink(missing_ok=True)

    # ---------- proposta ----------
    def _validate(self, kind: str, payload: dict) -> None:
        if kind not in ("skill", "memory"):
            raise PendingError("kind deve ser 'skill' ou 'memory'.")
        if not isinstance(payload, dict):
            raise PendingError("payload deve ser um objeto.")
        allowed = SKILL_ACTIONS if kind == "skill" else MEMORY_ACTIONS
        if payload.get("action") not in allowed:
            raise PendingError(f"action deve ser um de: {', '.join(allowed)}.")
        if kind == "memory" and payload.get("target") not in ("memory", "user"):
            raise PendingError("target deve ser 'memory' ou 'user'.")
        if kind == "skill" and not isinstance(payload.get("name"), str):
            raise PendingError("name da skill é obrigatório.")
        for key in SCANNED_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                reason = scan.check_text(value)
                if reason:
                    raise PendingError(f"Conteúdo bloqueado pela varredura de segurança ({key}): {reason}.")
        if kind == "skill" and payload.get("path"):
            try:
                scan.check_path(payload["path"])
            except scan.Unsafe as err:
                raise PendingError(f"Caminho recusado: {err}.") from None

    def propose(self, kind: str, payload: dict, author: str) -> str:
        self._validate(kind, payload)
        pid = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        record = {"id": pid, "kind": kind, "payload": payload, "author": author or "", "created": _now(),
                  "status": "pending", "last_error": ""}
        if kind == "skill":
            try:
                locked = self.skills.is_locked(payload["name"])
            except SkillError as err:
                raise PendingError(str(err)) from None
            if locked:
                record.update(status="rejected", reason=f"skill travada ({payload['name']}): o agente não pode alterá-la",
                              decided=_now())
                self._write("rejected", record)
                return pid
        self._write("pending", record)
        return pid

    # ---------- consulta ----------
    @staticmethod
    def _summary(record: dict) -> str:
        payload = record["payload"]
        if record["kind"] == "skill":
            return f"{payload.get('action')} {payload.get('name')}" + (f" {payload['path']}" if payload.get("path") else "")
        detail = payload.get("text") or payload.get("new") or payload.get("old") or ""
        return f"{payload.get('action')} {payload.get('target')}: {detail[:60]}"

    def list(self) -> list[dict]:
        folder = self._folder("pending")
        if not folder.is_dir():
            return []
        items = []
        for path in sorted(folder.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            items.append({"id": record["id"], "kind": record["kind"], "author": record["author"],
                          "created": record["created"], "summary": self._summary(record),
                          "last_error": record.get("last_error", "")})
        return items

    def open_for(self, name: str) -> list[dict]:
        """Propostas de skill ainda pendentes para a skill `name` (id + payload)."""
        folder = self._folder("pending")
        found = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["kind"] == "skill" and record["payload"].get("name") == name:
                    found.append({"id": record["id"], "payload": record["payload"]})
        return found

    def status(self, pid: str) -> dict:
        for state in ("pending", "applied", "rejected"):
            path = self._path(state, pid)
            if path.is_file():
                record = json.loads(path.read_text(encoding="utf-8"))
                return {"id": pid, "status": record["status"], "reason": record.get("reason", ""),
                        "last_error": record.get("last_error", "")}
        raise PendingError(f"Proposta não encontrada: {pid}")

    @staticmethod
    def _udiff(before: str, after: str, label: str) -> str:
        lines = difflib.unified_diff(before.splitlines(), after.splitlines(), f"{label} (atual)", f"{label} (proposto)",
                                     lineterm="", n=2)
        return "\n".join(lines) or "(sem diferença)"

    def diff(self, pid: str) -> str:
        record = self._load(pid)
        payload = record["payload"]
        header = f"# {record['kind']} · {self._summary(record)} · autor={record['author'] or '?'}"
        if isinstance(payload.get("reason"), str) and payload["reason"]:
            header += f"\n# motivo: {payload['reason']}"
        try:
            body = self._skill_diff(payload) if record["kind"] == "skill" else self._memory_diff(payload)
        except SkillError as err:
            body = f"(não aplicável agora: {err})"
        return header + "\n" + body

    def _skill_diff(self, payload: dict) -> str:
        action, name = payload["action"], payload["name"]
        if action == "create":
            return self._udiff("", payload.get("body", ""), f"{name}/SKILL.md")
        if action == "delete":
            return self._udiff(self.skills.view(name), "", f"{name}/SKILL.md")
        path = payload.get("path")
        if action == "patch":
            before = self.skills.view(name, path) if path else self.skills.view(name)
            after = SkillStore.replace_once(before, payload.get("old", ""), payload.get("new", ""))
            return self._udiff(before, after, f"{name}/{path or 'SKILL.md'}")
        if action == "write_file":
            try:
                before = self.skills.view(name, path)
            except SkillError:
                before = ""
            return self._udiff(before, payload.get("content", ""), f"{name}/{path}")
        return self._udiff(self.skills.view(name, path), "", f"{name}/{path}")  # remove_file

    def _memory_diff(self, payload: dict) -> str:
        target, action = payload["target"], payload["action"]
        entries = self.memory.read(target)
        if action == "add":
            after = entries + [payload.get("text", "").strip()]
        elif action == "remove":
            after = [e for e in entries if payload.get("old", "") not in e]
        else:
            after = [e.replace(payload.get("old", ""), payload.get("new", ""), 1) if payload.get("old", "") in e else e
                     for e in entries]
        return self._udiff("\n".join(entries), "\n".join(after), target.upper())

    # ---------- decisão (só a CLI chama) ----------
    def _apply(self, record: dict) -> dict:
        payload = record["payload"]
        if record["kind"] == "skill":
            return self.skills.apply(payload)
        action, target = payload["action"], payload["target"]
        if action == "add":
            result = self.memory.add(target, payload.get("text", ""))
        elif action == "patch":
            result = self.memory.patch(target, payload.get("old", ""), payload.get("new", ""))
        else:
            result = self.memory.remove(target, payload.get("old", ""))
        if not result["ok"]:
            raise PendingError(result["message"])
        return result

    def approve(self, pid: str) -> dict:
        record = self._load(pid)
        try:
            result = self._apply(record)
        except (SkillError, PendingError) as err:
            record["last_error"] = str(err)
            self._write("pending", record)
            raise PendingError(f"Não foi possível aplicar {pid}: {err}") from None
        record.update(status="applied", decided=_now(), last_error="", result=result)
        self._move(record, "applied")
        return result

    def reject(self, pid: str, reason: str = "") -> None:
        record = self._load(pid)
        record.update(status="rejected", reason=reason, decided=_now())
        self._move(record, "rejected")
