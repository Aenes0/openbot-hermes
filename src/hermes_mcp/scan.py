"""Varredura de segurança: injeção de prompt, segredos, Unicode invisível e caminhos perigosos."""
from __future__ import annotations

import re
import unicodedata

INJECTION = [
    (re.compile(r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,30}"
                r"\b(instructions?|rules?|prompts?|guidelines)\b", re.I | re.S),
     "tentativa de injeção de prompt (ignorar instruções)"),
    (re.compile(r"\bignor(e|ar|em)\b.{0,40}\b(regras|instru[cç][õo]es|instru[cç][aã]o|diretrizes|prompt)\b", re.I | re.S),
     "tentativa de injeção de prompt (ignorar regras)"),
    (re.compile(r"\bdisregard\b.{0,40}\b(instructions?|rules?|system prompt|prompt|guidelines)\b", re.I | re.S),
     "tentativa de injeção de prompt (disregard)"),
    (re.compile(r"\b(you are now|voc[eê] agora [eé])\b.{0,60}\b(unrestricted|no restrictions|without restrictions|"
                r"sem restri[cç][õo]es|jailbreak|dan)\b", re.I | re.S),
     "tentativa de trocar o papel do agente"),
    (re.compile(r"\b(reveal|show|print|leak|exfiltrate|revele|mostre|imprima|vaze)\b.{0,30}"
                r"\b(system prompt|prompt do sistema|your instructions|suas instru[cç][õo]es)\b", re.I | re.S),
     "pedido para vazar o prompt do sistema"),
    (re.compile(r"</?\s*(system|assistant|developer)\s*>", re.I), "marcação falsa de mensagem de sistema"),
    (re.compile(r"\b(curl|wget)\b[^\n|]{0,200}\|\s*(sh|bash|zsh)\b", re.I), "baixar e executar script"),
    (re.compile(r"\b(send|post|upload|envie|enviar|mande)\b.{0,50}\b(senha|password|token|api[ _-]?key|credentials?|credenciais)\b"
                r".{0,40}https?://", re.I | re.S),
     "tentativa de exfiltração de credenciais"),
]

SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "chave de API (sk-)"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "chave de API do Google"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), "token do GitHub"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "token do Slack"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "chave da AWS"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "chave privada"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"), "token Bearer"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "token JWT"),
    (re.compile(r"\b(api[_-]?key|secret|token|password|senha)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_.-]{16,}", re.I),
     "credencial atribuída em texto"),
]

ZWJ = "‍"


class Unsafe(Exception):
    """Caminho recusado (traversal, absoluto ou com caractere proibido)."""


def _invisible(text: str) -> str | None:
    for index, char in enumerate(text):
        category = unicodedata.category(char)
        if char in "\n\t\r":
            continue
        if category == "Cc":
            return f"caractere de controle invisível (U+{ord(char):04X})"
        if category == "Cf":
            if char == ZWJ and 0 < index < len(text) - 1 \
                    and unicodedata.category(text[index - 1]) in ("So", "Sk", "Mn") \
                    and unicodedata.category(text[index + 1]) == "So":
                continue  # junção de emoji (família, profissões) é legítima
            return f"caractere Unicode invisível (U+{ord(char):04X})"
    return None


def check_text(text: str) -> str | None:
    """Devolve o motivo do bloqueio, ou None se o texto pode ser gravado."""
    invisible = _invisible(text)
    if invisible:
        return invisible
    for pattern, reason in SECRETS:
        if pattern.search(text):
            return f"parece conter segredo: {reason}"
    for pattern, reason in INJECTION:
        if pattern.search(text):
            return reason
    return None


def check_path(rel: str) -> str:
    """Normaliza um caminho relativo de arquivo de skill ou levanta Unsafe."""
    if not isinstance(rel, str) or not rel.strip():
        raise Unsafe("caminho vazio")
    if "\x00" in rel or "\\" in rel:
        raise Unsafe("caminho com caractere proibido")
    if rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel):
        raise Unsafe("caminho absoluto não é permitido")
    parts = rel.split("/")
    if ".." in parts:
        raise Unsafe("caminho com '..' não é permitido")
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        raise Unsafe("caminho vazio")
    return "/".join(clean)


def redact_secrets(text: str) -> str:
    """Troca por [segredo omitido] o que parece chave/token (usado antes de indexar sessões)."""
    for pattern, _ in SECRETS:
        text = pattern.sub("[segredo omitido]", text)
    return text
