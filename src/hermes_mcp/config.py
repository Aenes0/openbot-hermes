"""Configuração: pasta de estado (0700), caminho do banco do OpenBot e id do agente chamador."""
from __future__ import annotations

import os
import re
from pathlib import Path

AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ConfigError(Exception):
    """Configuração inválida; a mensagem diz o que fazer."""


def _env(env: dict | None) -> dict:
    return os.environ if env is None else env


def _config_base(env: dict) -> Path:
    """~/.config literal (como o plano manda). XDG_CONFIG_HOME é ignorado de propósito: sandboxes (Flatpak) o
    redirecionam e o OpenBot e este estado ficam em ~/.config."""
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".config"


def home(env: dict | None = None) -> Path:
    """Pasta de estado do servidor (memória, skills, fila, índices). Criada com modo 0700."""
    env = _env(env)
    explicit = env.get("HERMES_MCP_HOME")
    folder = Path(explicit).expanduser() if explicit else _config_base(env) / "openbot-hermes"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def openbot_db(env: dict | None = None) -> Path:
    """Banco do OpenBot. Este projeto só o abre em modo somente leitura."""
    env = _env(env)
    explicit = env.get("HERMES_MCP_OPENBOT_DB")
    if explicit:
        return Path(explicit).expanduser()
    return _config_base(env) / "OpenBot" / "openbot.db"


def agent_id(env: dict | None = None) -> str:
    """Id do agente que chama (vazio = sem filtro por agente)."""
    value = (_env(env).get("HERMES_MCP_AGENT") or "").strip()
    if value and not AGENT_RE.match(value):
        raise ConfigError("HERMES_MCP_AGENT inválido: use letras, números, ponto, hífen e sublinhado.")
    return value


def write_approval(env: dict | None = None) -> bool:
    """Escritas do agente viram propostas (padrão). Só 'false'/'0'/'no' liga a escrita direta."""
    value = (_env(env).get("HERMES_MCP_WRITE_APPROVAL") or "true").strip().lower()
    return value not in ("false", "0", "no", "off")


def promote_n(env: dict | None = None) -> int:
    """N de execuções boas exigidas para descer de tier (padrão 5)."""
    raw = (_env(env).get("HERMES_MCP_PROMOTE_N") or "5").strip()
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("HERMES_MCP_PROMOTE_N deve ser um número inteiro maior que zero.") from None
    if value < 1:
        raise ConfigError("HERMES_MCP_PROMOTE_N deve ser um número inteiro maior que zero.")
    return value
