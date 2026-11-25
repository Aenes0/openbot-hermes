"""Memória curada: MEMORY (do agente) e USER (do Alexandre), com limite de caracteres. Sem ação 'replace'."""
from __future__ import annotations

import os
from pathlib import Path

from . import scan

LIMITS = {"memory": 2200, "user": 1375}
FILES = {"memory": "MEMORY.md", "user": "USER.md"}
SEPARATOR = "\n§\n"


class Memory:
    def __init__(self, home: Path):
        self.dir = Path(home) / "memories"
        self._frozen: dict | None = None

    # ---------- leitura ----------
    def _path(self, target: str) -> Path:
        return self.dir / FILES[target]

    def read(self, target: str) -> list[str]:
        """Entradas atuais (leitura ao vivo)."""
        if target not in FILES:
            raise ValueError(f"target deve ser um de: {', '.join(FILES)}")
        try:
            text = self._path(target).read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [entry for entry in text.split(SEPARATOR) if entry.strip()]

    def snapshot(self) -> dict:
        """Cópia congelada na primeira chamada desta instância (como o Hermes: o prompt não muda no meio da sessão)."""
        if self._frozen is None:
            self._frozen = {name: SEPARATOR.join(self.read(name)) for name in FILES}
        return dict(self._frozen)

    # ---------- escrita ----------
    @staticmethod
    def _result(ok: bool, message: str, entries: list[str], target: str) -> dict:
        return {"ok": ok, "message": message, "chars": len(SEPARATOR.join(entries)), "limit": LIMITS[target]}

    def _fail(self, target: str, message: str) -> dict:
        entries = self.read(target) if target in FILES else []
        return {"ok": False, "message": message, "chars": len(SEPARATOR.join(entries)),
                "limit": LIMITS.get(target, 0)}

    def _save(self, target: str, entries: list[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        path = self._path(target)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(SEPARATOR.join(entries))
        os.replace(tmp, path)

    def _validate(self, target: str, text: str, what: str = "texto") -> str | None:
        """Devolve mensagem de erro ou None."""
        if target not in FILES:
            return f"target deve ser um de: {', '.join(FILES)}"
        if not isinstance(text, str) or not text.strip():
            return f"{what} vazio"
        if "§" in text:
            return f"{what} não pode conter o caractere § (é o separador de entradas)"
        reason = scan.check_text(text)
        if reason:
            return f"{what} bloqueado pela varredura de segurança: {reason}"
        return None

    def _commit(self, target: str, entries: list[str], message: str, dry_run: bool = False) -> dict:
        total = len(SEPARATOR.join(entries))
        if total > LIMITS[target]:
            return self._fail(target, f"Estouraria o limite de {LIMITS[target]} caracteres de {target.upper()} "
                                      f"({total}). Consolide ou remova entradas antes de adicionar.")
        if not dry_run:
            self._save(target, entries)
        return self._result(True, message, entries, target)

    def add(self, target: str, text: str, dry_run: bool = False) -> dict:
        error = self._validate(target, text)
        if error:
            return self._fail(target, error)
        entry = text.strip()
        entries = self.read(target)
        if entry in entries:
            return self._result(True, "A entrada já existe; no duplicate added.", entries, target)
        return self._commit(target, entries + [entry], "Entrada adicionada.", dry_run)

    def _match(self, target: str, snippet: str) -> tuple[int | None, str | None]:
        """Índice da única entrada que contém o trecho, ou (None, motivo)."""
        if not isinstance(snippet, str) or not snippet:
            return None, "trecho vazio"
        entries = self.read(target)
        hits = [i for i, entry in enumerate(entries) if snippet in entry]
        total = sum(entries[i].count(snippet) for i in hits)
        if not hits:
            return None, "Trecho não encontrado em nenhuma entrada."
        if total > 1:
            return None, f"Trecho ambíguo ({total} ocorrências); use um trecho único."
        return hits[0], None

    def patch(self, target: str, old: str, new: str, dry_run: bool = False) -> dict:
        error = self._validate(target, new, "novo texto")
        if error:
            return self._fail(target, error)
        index, reason = self._match(target, old)
        if reason:
            return self._fail(target, reason)
        entries = self.read(target)
        entries[index] = entries[index].replace(old, new.strip() if old == entries[index] else new, 1)
        if not entries[index].strip():
            return self._fail(target, "A entrada ficaria vazia; use remove.")
        return self._commit(target, entries, "Entrada corrigida.", dry_run)

    def remove(self, target: str, old: str, dry_run: bool = False) -> dict:
        if target not in FILES:
            return self._fail(target, f"target deve ser um de: {', '.join(FILES)}")
        index, reason = self._match(target, old)
        if reason:
            return self._fail(target, reason)
        entries = self.read(target)
        del entries[index]
        return self._commit(target, entries, "Entrada removida.", dry_run)
