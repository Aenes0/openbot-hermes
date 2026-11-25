"""Skills no padrão agentskills.io: <categoria>/<nome>/SKILL.md + references/templates/scripts/examples/assets.

`apply` grava direto; quem chama é a aprovação (Pending) ou a CLI. O agente nunca chama `apply`.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import scan
from .fsutil import atomic_write

TIERS = ("strong", "sonnet", "haiku", "script")
SUBDIRS = ("references", "templates", "scripts", "examples", "assets")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_DESCRIPTION = 1024
MAX_FILE_CHARS = 200_000
LIST_FIELDS = ("requires_toolsets", "requires_tools", "fallback_for_toolsets", "fallback_for_tools")
PLAIN_RE = re.compile(r"^[A-Za-z0-9_./][A-Za-z0-9 _./+-]*$")
KEYWORDS = {"true", "false", "null", "yes", "no", "on", "off", "~"}


class SkillError(Exception):
    """Operação recusada; a mensagem é escrita para o agente ou para o Alexandre."""


# ---------- frontmatter YAML mínimo (sem PyYAML) ----------
def _split_list(inner: str) -> list[str]:
    items, current, quoted, escaped = [], [], False, False
    for char in inner:
        if quoted:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def _scalar(raw: str):
    raw = raw.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise SkillError("Frontmatter inválido: lista sem ']'.")
        return [_scalar(item) for item in _split_list(raw[1:-1])]
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            raise SkillError("Frontmatter inválido: texto entre aspas malformado.") from None
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    return raw


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        current = len(line) - len(line.lstrip(" "))
        if current < indent:
            break
        if current > indent:
            raise SkillError("Frontmatter inválido: indentação inesperada.")
        match = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", line.strip())
        if not match:
            raise SkillError(f"Frontmatter inválido na linha: {line.strip()[:60]}")
        key, value = match.group(1), match.group(2)
        index += 1
        if value is None or value == "":
            child_indent = indent + 2
            if index < len(lines) and lines[index].strip() and \
                    len(lines[index]) - len(lines[index].lstrip(" ")) >= child_indent:
                result[key], index = _parse_block(lines, index, child_indent)
            else:
                result[key] = ""
        else:
            result[key] = _scalar(value)
    return result, index


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Separa o bloco `---` do corpo. Sem bloco: ({}, texto)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    after = end + 4
    if after < len(text) and text[after] not in "\r\n":
        return {}, text
    block = text[4:end]
    body = text[after:].lstrip("\r\n") if after < len(text) else ""
    meta, _ = _parse_block(block.split("\n"), 0, 0)
    return meta, body


def _fmt(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(item) for item in value) + "]"
    text = str(value)
    if PLAIN_RE.match(text) and text == text.strip() and text.lower() not in KEYWORDS:
        return text
    return json.dumps(text, ensure_ascii=False)


def _dump(data: dict, indent: int = 0) -> list[str]:
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(" " * indent + f"{key}:")
            lines.extend(_dump(value, indent + 2))
        else:
            lines.append(" " * indent + f"{key}: {_fmt(value)}")
    return lines


def render(meta: dict, body: str) -> str:
    return "---\n" + "\n".join(_dump(meta)) + "\n---\n" + body


def bump_version(version: str) -> str:
    parts = str(version).split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return "1.0.1"


def _hermes(meta: dict) -> dict:
    block = meta.get("metadata")
    hermes = block.get("hermes") if isinstance(block, dict) else None
    return hermes if isinstance(hermes, dict) else {}


# ---------- armazenamento ----------
class SkillStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    # -- localização
    def _check_name(self, name) -> str:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SkillError("Nome de skill inválido: use minúsculas, números e hífen (até 64 caracteres).")
        return name

    def _find(self, name: str) -> Path | None:
        self._check_name(name)
        if not self.root.is_dir():
            return None
        for category in sorted(self.root.iterdir()):
            candidate = category / name
            if category.is_dir() and not category.name.startswith(".") and (candidate / "SKILL.md").is_file():
                return candidate
        return None

    def _dir(self, name: str) -> Path:
        found = self._find(name)
        if found is None:
            raise SkillError(f"Skill não encontrada: {name}")
        return found

    def _read(self, directory: Path) -> tuple[dict, str]:
        return parse_frontmatter((directory / "SKILL.md").read_text(encoding="utf-8"))

    # -- travas
    def _lock_file(self, name: str) -> Path:
        return self.root / ".locks" / self._check_name(name)

    def lock(self, name: str) -> None:
        self._dir(name)
        atomic_write(self._lock_file(name), "locked\n")

    def unlock(self, name: str) -> None:
        self._dir(name)
        self._lock_file(name).unlink(missing_ok=True)

    def is_locked(self, name: str) -> bool:
        return self._lock_file(name).exists()

    # -- leitura
    def list(self, toolsets=None, tools=None) -> list[dict]:
        """Nível 0: só nome, descrição, categoria e tier. None = não filtra por aquela dimensão."""
        have_tools = None if tools is None else set(tools)
        have_sets = None if toolsets is None else set(toolsets)
        items = []
        if not self.root.is_dir():
            return items
        for category in sorted(self.root.iterdir()):
            if not category.is_dir() or category.name.startswith("."):
                continue
            for directory in sorted(category.iterdir()):
                if not (directory / "SKILL.md").is_file():
                    continue
                try:
                    meta, _ = self._read(directory)
                except SkillError:
                    continue
                hermes = _hermes(meta)
                if not self._active(hermes, have_sets, have_tools):
                    continue
                items.append({"name": meta.get("name", directory.name), "description": meta.get("description", ""),
                              "category": hermes.get("category") or category.name,
                              "cost_tier": hermes.get("cost_tier") or "strong"})
        return items

    @staticmethod
    def _active(hermes: dict, have_sets: set | None, have_tools: set | None) -> bool:
        def values(key: str) -> list:
            value = hermes.get(key) or []
            return value if isinstance(value, list) else [value]

        if have_tools is not None:
            if any(t not in have_tools for t in values("requires_tools")):
                return False
            if any(t in have_tools for t in values("fallback_for_tools")):
                return False
        if have_sets is not None:
            if any(t not in have_sets for t in values("requires_toolsets")):
                return False
            if any(t in have_sets for t in values("fallback_for_toolsets")):
                return False
        return True

    def meta(self, name: str) -> dict:
        meta, _ = self._read(self._dir(name))
        return meta

    def files(self, name: str) -> list[str]:
        directory = self._dir(name)
        found = []
        for sub in SUBDIRS:
            base = directory / sub
            if base.is_dir() and not base.is_symlink():
                found.extend(sorted(str(p.relative_to(directory)) for p in base.rglob("*") if p.is_file()))
        return found

    def _resolve_file(self, directory: Path, path: str) -> tuple[str, Path]:
        try:
            rel = scan.check_path(path)
        except scan.Unsafe as err:
            raise SkillError(f"Caminho recusado: {err}.") from None
        if rel.split("/")[0] not in SUBDIRS or "/" not in rel:
            raise SkillError(f"Caminho deve ficar dentro de: {', '.join(SUBDIRS)} (ex.: references/arquivo.md).")
        target = directory / rel
        if not target.resolve().is_relative_to(directory.resolve()):
            raise SkillError("Caminho recusado: sai da pasta da skill.")
        for parent in [target.parent, *target.parent.parents]:
            if parent == directory:
                break
            if parent.is_symlink():
                raise SkillError("Caminho recusado: passa por link simbólico.")
        return rel, target

    def view(self, name: str, path: str | None = None) -> str:
        directory = self._dir(name)
        if path is None:
            return (directory / "SKILL.md").read_text(encoding="utf-8")
        _, target = self._resolve_file(directory, path)
        if not target.is_file():
            raise SkillError(f"Arquivo não encontrado na skill: {path}")
        return target.read_text(encoding="utf-8")

    # -- escrita (só aprovação/CLI)
    def _history(self, name: str, directory: Path) -> None:
        meta, _ = self._read(directory)
        version = str(meta.get("version") or "0.0.0")
        target = self.root / ".history" / name / f"{version}.md"
        if target.exists():
            target = target.with_name(f"{version}-{int(time.time())}.md")
        atomic_write(target, (directory / "SKILL.md").read_text(encoding="utf-8"))

    @staticmethod
    def _scan(text: str, what: str) -> None:
        reason = scan.check_text(text)
        if reason:
            raise SkillError(f"{what} bloqueado pela varredura de segurança: {reason}.")

    def _validate_meta(self, meta: dict, name: str) -> None:
        if meta.get("name") != name:
            raise SkillError("O frontmatter precisa manter name igual ao nome da skill.")
        description = meta.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > MAX_DESCRIPTION:
            raise SkillError(f"description é obrigatória e tem no máximo {MAX_DESCRIPTION} caracteres.")
        tier = _hermes(meta).get("cost_tier") or "strong"
        if tier not in TIERS:
            raise SkillError(f"cost_tier deve ser um de: {', '.join(TIERS)}.")

    def apply(self, op: dict) -> dict:
        action = op.get("action")
        name = self._check_name(op.get("name"))
        handler = {"create": self._create, "patch": self._patch, "delete": self._delete,
                   "write_file": self._write_file, "remove_file": self._remove_file}.get(action)
        if handler is None:
            raise SkillError("action deve ser um de: create, patch, delete, write_file, remove_file.")
        if action != "create" and self.is_locked(name):
            raise SkillError(f"A skill {name} está travada pelo Alexandre; ela não aceita alterações.")
        return handler(name, op)

    def _create(self, name: str, op: dict) -> dict:
        if self._find(name):
            raise SkillError(f"Já existe uma skill chamada {name}.")
        category = op.get("category")
        if not isinstance(category, str) or not NAME_RE.match(category):
            raise SkillError("category inválida: use minúsculas, números e hífen.")
        body = op.get("body")
        if not isinstance(body, str) or not body.strip():
            raise SkillError("body (corpo do SKILL.md) é obrigatório.")
        hermes = {"tags": list(op.get("tags") or []), "category": category}
        for key in LIST_FIELDS:
            hermes[key] = list(op.get(key) or [])
        hermes["cost_tier"] = op.get("cost_tier") or "strong"
        meta = {"name": name, "description": op.get("description") or "", "version": "1.0.0",
                "platforms": list(op.get("platforms") or []), "metadata": {"hermes": hermes}}
        self._validate_meta(meta, name)
        text = render(meta, body if body.endswith("\n") else body + "\n")
        self._scan(text, "Conteúdo da skill")
        atomic_write(self.root / category / name / "SKILL.md", text, 0o644)
        return {"ok": True, "name": name, "version": "1.0.0", "message": f"Skill {name} criada."}

    def _rewrite(self, name: str, directory: Path, meta: dict, body: str) -> dict:
        version = bump_version(str(meta.get("version") or "1.0.0"))
        meta = dict(meta, version=version)
        atomic_write(directory / "SKILL.md", render(meta, body), 0o644)
        return {"ok": True, "name": name, "version": version, "message": f"Skill {name} agora na versão {version}."}

    def _patch(self, name: str, op: dict) -> dict:
        directory = self._dir(name)
        old, new = op.get("old"), op.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise SkillError("patch exige old (trecho atual, não vazio) e new.")
        self._scan(new, "Texto novo")
        path = op.get("path")
        if path:
            rel, target = self._resolve_file(directory, path)
            if not target.is_file():
                raise SkillError(f"Arquivo não encontrado na skill: {path}")
            text = target.read_text(encoding="utf-8")
            updated = self.replace_once(text, old, new)
            self._scan(updated, "Arquivo")
            meta, body = self._read(directory)
            self._history(name, directory)
            atomic_write(target, updated, 0o644)
            return self._rewrite(name, directory, meta, body)
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
        updated = self.replace_once(text, old, new)
        meta, body = parse_frontmatter(updated)
        self._validate_meta(meta, name)
        if _hermes(meta).get("category") != _hermes(self._read(directory)[0]).get("category"):
            raise SkillError("A categoria não pode mudar por patch.")
        original_meta, _ = self._read(directory)
        meta["version"] = original_meta.get("version") or "1.0.0"
        self._history(name, directory)
        return self._rewrite(name, directory, meta, body)

    @staticmethod
    def replace_once(text: str, old: str, new: str) -> str:
        count = text.count(old)
        if count == 0:
            raise SkillError("Trecho não encontrado; leia a versão atual da skill e tente de novo.")
        if count > 1:
            raise SkillError(f"Trecho ambíguo ({count} ocorrências); use um trecho único.")
        return text.replace(old, new, 1)

    def _delete(self, name: str, op: dict) -> dict:
        directory = self._dir(name)
        self._history(name, directory)
        shutil.rmtree(directory)
        return {"ok": True, "name": name, "version": None, "message": f"Skill {name} removida (cópia em .history)."}

    def _write_file(self, name: str, op: dict) -> dict:
        directory = self._dir(name)
        rel, target = self._resolve_file(directory, op.get("path"))
        content = op.get("content")
        if not isinstance(content, str):
            raise SkillError("write_file exige content (texto).")
        if len(content) > MAX_FILE_CHARS:
            raise SkillError(f"Arquivo grande demais (máximo {MAX_FILE_CHARS} caracteres).")
        self._scan(content, "Arquivo")
        meta, body = self._read(directory)
        self._history(name, directory)
        atomic_write(target, content, 0o644)
        return self._rewrite(name, directory, meta, body)

    def _remove_file(self, name: str, op: dict) -> dict:
        directory = self._dir(name)
        rel, target = self._resolve_file(directory, op.get("path"))
        if not target.is_file():
            raise SkillError(f"Arquivo não encontrado na skill: {op.get('path')}")
        meta, body = self._read(directory)
        self._history(name, directory)
        target.unlink()
        return self._rewrite(name, directory, meta, body)
