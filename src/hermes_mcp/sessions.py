"""Busca textual (FTS5) nas conversas do OpenBot. O banco do OpenBot só é aberto em modo somente leitura."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from . import scan

AUTHORS = ("user", "assistant")
ITEM_TYPES = ("final_answer", "agentMessage")
MAX_TEXT = 20_000
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SOURCE_QUERY = (
    "SELECT m.thread_id, m.message_id, m.author, m.created_at, m.message_json, t.agent_id "
    "FROM projection_thread_messages m LEFT JOIN projection_threads t ON t.thread_id = m.thread_id "
    "WHERE m.created_at >= ? AND m.status = 'completed' AND m.author IN ('user', 'assistant') "
    "AND (m.item_type IS NULL OR m.item_type IN ('final_answer', 'agentMessage')) "
    "ORDER BY m.created_at, m.message_id")


class SessionsError(Exception):
    """Erro esperado ao ler o OpenBot ou o índice; a mensagem é escrita para o agente."""


def open_readonly(path: Path) -> sqlite3.Connection:
    """Abre um SQLite em modo somente leitura (URI mode=ro) e recusa qualquer escrita."""
    path = Path(path)
    if not path.is_file():
        raise SessionsError(f"Banco do OpenBot não encontrado em {path}.")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only = ON")
    return conn


class Sessions:
    def __init__(self, home: Path, openbot_db: Path):
        self.home = Path(home)
        self.openbot_db = Path(openbot_db)
        self.index_path = self.home / "sessions.db"

    # ---------- origem (OpenBot, somente leitura) ----------
    def _open_source(self) -> sqlite3.Connection:
        return open_readonly(self.openbot_db)

    @staticmethod
    def _explain(err: sqlite3.Error) -> SessionsError:
        text = str(err)
        if "no such table" in text:
            return SessionsError("O banco do OpenBot não tem a tabela esperada (projection_thread_messages "
                                 f"/ projection_threads): {text}. A versão do OpenBot pode ter mudado.")
        if "locked" in text or "busy" in text:
            return SessionsError("O banco do OpenBot está bloqueado ou em uso agora; tente de novo em instantes.")
        return SessionsError(f"Não foi possível ler o banco do OpenBot: {text}.")

    # ---------- índice ----------
    def _index(self) -> sqlite3.Connection:
        self.home.mkdir(parents=True, exist_ok=True)
        fresh = not self.index_path.exists()
        conn = sqlite3.connect(self.index_path, timeout=5.0)
        if fresh:
            os.chmod(self.index_path, 0o600)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL, message_id TEXT NOT NULL, "
            "agent_id TEXT, author TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(thread_id, message_id));"
            "CREATE VIRTUAL TABLE IF NOT EXISTS msgs_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');"
            "CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        return conn

    @staticmethod
    def _text(message_json: str) -> str:
        try:
            data = json.loads(message_json)
        except ValueError:
            return ""
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            return ""
        return scan.redact_secrets(text.strip())[:MAX_TEXT]

    def sync(self) -> int:
        """Indexa as mensagens novas (incremental por created_at; repetidas são ignoradas). Devolve quantas entraram."""
        try:
            source = self._open_source()
        except sqlite3.Error as err:
            raise self._explain(err) from None
        try:
            index = self._index()
        except sqlite3.Error as err:
            source.close()
            raise SessionsError(f"Não foi possível abrir o índice de sessões: {err}.") from None
        added = 0
        try:
            row = index.execute("SELECT value FROM state WHERE key = 'watermark'").fetchone()
            watermark = row[0] if row else ""
            newest = watermark
            try:
                rows = source.execute(SOURCE_QUERY, (watermark,)).fetchall()
            except sqlite3.Error as err:
                raise self._explain(err) from None
            with index:
                for thread_id, message_id, author, created_at, message_json, agent_id in rows:
                    newest = max(newest, created_at)
                    text = self._text(message_json)
                    if not text:
                        continue
                    cursor = index.execute(
                        "INSERT OR IGNORE INTO msgs(thread_id, message_id, agent_id, author, created_at) "
                        "VALUES (?, ?, ?, ?, ?)", (thread_id, message_id, agent_id, author, created_at))
                    if cursor.rowcount == 1:
                        index.execute("INSERT INTO msgs_fts(rowid, text) VALUES (?, ?)", (cursor.lastrowid, text))
                        added += 1
                index.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('watermark', ?)", (newest,))
        finally:
            source.close()
            index.close()
        return added

    # ---------- busca ----------
    @staticmethod
    def _match_expression(query: str) -> str:
        tokens = TOKEN_RE.findall(query or "")
        if not tokens:
            raise SessionsError("A consulta não tem termos de busca (use palavras).")
        return " ".join('"' + token + '"' for token in tokens)

    def search(self, query: str, agent_id: str | None = None, limit: int = 10) -> list[dict]:
        match = self._match_expression(query)
        limit = max(1, min(int(limit), 50))
        if not self.index_path.is_file():
            return []
        sql = ("SELECT m.thread_id, m.message_id, m.author, m.created_at, snippet(msgs_fts, 0, '', '', '…', 40) "
               "FROM msgs_fts JOIN msgs m ON m.id = msgs_fts.rowid WHERE msgs_fts MATCH ?")
        params: list = [match]
        if agent_id:
            sql += " AND m.agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY rank, m.created_at DESC LIMIT ?"
        params.append(limit)
        index = self._index()
        try:
            rows = index.execute(sql, params).fetchall()
        except sqlite3.Error as err:
            raise SessionsError(f"Busca falhou: {err}.") from None
        finally:
            index.close()
        return [{"thread_id": t, "message_id": m, "author": a, "created_at": c, "snippet": s}
                for t, m, a, c, s in rows]

    def thread_messages(self, since: str = "") -> list[dict]:
        """Mensagens indexadas das conversas que tiveram atividade a partir de `since`, em ordem por conversa e hora."""
        if not self.index_path.is_file():
            return []
        index = self._index()
        try:
            rows = index.execute(
                "SELECT m.thread_id, m.message_id, m.agent_id, m.author, m.created_at, f.text FROM msgs m "
                "JOIN msgs_fts f ON f.rowid = m.id WHERE m.thread_id IN "
                "(SELECT thread_id FROM msgs WHERE created_at >= ?) ORDER BY m.thread_id, m.created_at, m.message_id",
                (since,)).fetchall()
        except sqlite3.Error as err:
            raise SessionsError(f"Leitura do índice falhou: {err}.") from None
        finally:
            index.close()
        return [{"thread_id": t, "message_id": m, "agent_id": a, "author": au, "created_at": c, "text": x}
                for t, m, a, au, c, x in rows]
