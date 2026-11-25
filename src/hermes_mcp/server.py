"""Servidor MCP via stdio (JSON-RPC, uma mensagem por linha). Só stdlib."""
from __future__ import annotations

import json
import sys
import traceback

from .config import ConfigError
from .pending import PendingError
from .sessions import SessionsError
from .skills import SkillError
from .tools import ToolError

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "openbot-hermes-mcp", "version": "0.1.0"}


class Server:
    def __init__(self, toolbox, stdin=None, stdout=None, stderr=None):
        self.toolbox = toolbox
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def _send(self, message: dict) -> None:
        self.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def _reply(self, request_id, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    @staticmethod
    def _tool_result(text: str, is_error: bool) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    def _call_tool(self, params: dict) -> dict:
        try:
            return self._tool_result(self.toolbox.call(params.get("name", ""), params.get("arguments")), False)
        except (ToolError, SkillError, SessionsError, PendingError, ConfigError) as err:  # escritas para o agente
            return self._tool_result(str(err), True)
        except Exception:  # noqa: BLE001 - nunca vazar detalhes internos ao agente
            print(traceback.format_exc(), file=self.stderr)
            return self._tool_result("Erro interno no servidor do Hermes. Veja o log do servidor.", True)

    def handle(self, message: dict) -> None:
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:  # notificação: sem resposta
            return
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
            self._reply(request_id, {"protocolVersion": requested, "capabilities": {"tools": {}},
                                     "serverInfo": SERVER_INFO})
        elif method == "ping":
            self._reply(request_id, {})
        elif method == "tools/list":
            self._reply(request_id, {"tools": self.toolbox.list_tools()})
        elif method == "tools/call":
            self._reply(request_id, self._call_tool(message.get("params") or {}))
        else:
            self._error(request_id, -32601, f"Método não suportado: {method}")

    def serve(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._error(None, -32700, "JSON inválido")
                continue
            if isinstance(message, dict):
                self.handle(message)
