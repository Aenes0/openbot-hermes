"""Linha de comando: serve (usado pelo OpenBot), check, pending e skills (só o Alexandre aplica)."""
from __future__ import annotations

import argparse
import sqlite3
import sys

from . import config, sessions as sessions_module
from .curator import Curator
from .ladder import Ladder, LadderError
from .ledger import Ledger, LedgerError
from .memory import Memory
from .pending import Pending, PendingError
from .router import Router
from .server import Server
from .sessions import Sessions, SessionsError
from .skills import SkillError, SkillStore
from .tools import HermesToolbox


class Parts:
    """Os componentes ligados à pasta de estado configurada."""

    def __init__(self, env=None):
        self.home = config.home(env)
        self.skills = SkillStore(self.home / "skills")
        self.memory = Memory(self.home)
        self.pending = Pending(self.home, self.skills, self.memory)
        self.sessions = Sessions(self.home, config.openbot_db(env))
        self.agent = config.agent_id(env)
        self.approval = config.write_approval(env)
        self.ledger = Ledger(self.home, config.openbot_db(env))
        self.ladder = Ladder(self.ledger, self.skills, self.pending)
        self.router = Router(self.ledger, self.skills)
        self.curator = Curator(self.sessions, self.pending, self.skills)


def build(env=None) -> tuple[HermesToolbox, Parts]:
    parts = Parts(env)
    toolbox = HermesToolbox(parts.memory, parts.skills, parts.pending, parts.sessions,
                            agent_id=parts.agent, write_approval=parts.approval)
    parts.curator.register(toolbox)
    parts.router.register(toolbox)
    return toolbox, parts


def cmd_serve(args) -> int:
    try:
        toolbox, parts = build()
    except config.ConfigError as err:
        print(f"Configuração incompleta: {err}", file=sys.stderr)
        return 2
    print(f"openbot-hermes-mcp: agente={parts.agent or '(todos)'} aprovação={'ligada' if parts.approval else 'desligada'} "
          f"estado={parts.home}", file=sys.stderr)
    Server(toolbox).serve()
    return 0


def cmd_check(args) -> int:
    try:
        _, parts = build()
    except config.ConfigError as err:
        print(f"FALHOU: {err}")
        return 1
    ok = True
    print(f"Pasta de estado: OK ({parts.home}, modo 0700)")
    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        print("SQLite FTS5: OK")
    except Exception as err:  # noqa: BLE001
        ok = False
        print(f"SQLite FTS5: FALHOU - {err}")
    try:
        conn = sessions_module.open_readonly(parts.sessions.openbot_db)
        try:
            total = conn.execute("SELECT count(*) FROM projection_thread_messages").fetchone()[0]
            usable = conn.execute(
                "SELECT count(*) FROM projection_thread_messages WHERE status = 'completed' "
                "AND author IN ('user', 'assistant') AND (item_type IS NULL OR item_type IN ('final_answer', 'agentMessage'))"
            ).fetchone()[0]
            conn.execute("SELECT 1 FROM projection_threads LIMIT 1").fetchone()
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                ok = False
                print("Banco do OpenBot: FALHOU - a conexão não está em modo somente leitura")
            else:
                print(f"Banco do OpenBot (somente leitura): OK ({total} mensagens, {usable} indexáveis)")
        finally:
            conn.close()
    except SessionsError as err:
        ok = False
        print(f"Banco do OpenBot: FALHOU - {err}")
    except sqlite3.Error as err:
        ok = False
        print(f"Banco do OpenBot: FALHOU - {Sessions._explain(err)}")
    print(f"Skills: {len(parts.skills.list())} · propostas pendentes: {len(parts.pending.list())} · "
          f"aprovação: {'ligada' if parts.approval else 'desligada'}")
    print("RESULTADO: OK" if ok else "RESULTADO: FALHOU")
    return 0 if ok else 1


def _fail(err: Exception) -> int:
    print(f"Falhou: {err}", file=sys.stderr)
    return 1


def cmd_pending(args) -> int:
    parts = Parts()
    try:
        if args.action == "list":
            items = parts.pending.list()
            if not items:
                print("Nenhuma proposta pendente.")
            for item in items:
                note = f"  [último erro: {item['last_error']}]" if item["last_error"] else ""
                print(f"{item['id']}  {item['kind']:<6} {item['author'] or '?':<24} {item['summary']}{note}")
        elif args.action == "show":
            print(parts.pending.diff(args.id))
        elif args.action == "approve":
            result = parts.pending.approve(args.id)
            print(f"Aplicado: {result.get('message', 'ok')}")
        else:
            parts.pending.reject(args.id, args.reason or "")
            print(f"Rejeitada: {args.id}")
    except (PendingError, SkillError) as err:
        return _fail(err)
    return 0


def cmd_skills(args) -> int:
    parts = Parts()
    try:
        if args.action == "list":
            items = parts.skills.list()
            if not items:
                print("Nenhuma skill.")
            for item in items:
                lock = "  [travada]" if parts.skills.is_locked(item["name"]) else ""
                print(f"{item['category']}/{item['name']}  tier={item['cost_tier']}{lock}  {item['description']}")
        elif args.action == "lock":
            parts.skills.lock(args.name)
            print(f"Skill travada: {args.name} (o agente não consegue mais propor mudanças).")
        else:
            parts.skills.unlock(args.name)
            print(f"Skill destravada: {args.name}")
    except SkillError as err:
        return _fail(err)
    return 0


def cmd_ledger(args) -> int:
    parts = Parts()
    try:
        if args.action == "record":
            run_id = parts.ledger.record(args.skill, args.task_type, args.tier, args.outcome, args.source, args.ref)
            print(f"Registrado (execução #{run_id}).")
        elif args.action == "stats":
            parts.ledger.ingest()
            print(parts.ledger.stats(args.skill, args.tier))
        else:
            warnings = parts.ledger.warnings(args.skill)
            if not warnings:
                print("Nenhum aviso.")
            for item in warnings:
                print(f"{item['at']}  {item['skill']}: {item['message']}")
    except (LedgerError, SessionsError) as err:
        return _fail(err)
    return 0


def cmd_ladder(args) -> int:
    parts = Parts()
    try:
        if args.action == "crystallize":
            with open(args.file, encoding="utf-8") as handle:
                print(f"Proposta criada: {parts.ladder.crystallize(args.skill, handle.read())}")
            return 0
        parts.ledger.ingest()
        results = parts.ladder.review_all() if args.skill is None else [parts.ladder.review(args.skill)]
        for item in results:
            extra = f" proposta={item['proposal_id']}" if item.get("proposal_id") else ""
            print(f"{item['skill']}: {item['action']} {item.get('from', '')}{'->' + item['to'] if item.get('to') else ''}"
                  f"{extra} - {item.get('reason', '')}")
    except (LadderError, SessionsError, OSError) as err:
        return _fail(err)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openbot-hermes-mcp")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Servidor MCP via stdio (padrão)").set_defaults(run=cmd_serve)
    sub.add_parser("check", help="Confere estado, FTS5 e leitura do banco do OpenBot").set_defaults(run=cmd_check)
    pend = sub.add_parser("pending", help="Propostas dos agentes: list, show, approve, reject")
    pend_sub = pend.add_subparsers(dest="action", required=True)
    pend_sub.add_parser("list")
    for name in ("show", "approve", "reject"):
        item = pend_sub.add_parser(name)
        item.add_argument("id")
        if name == "reject":
            item.add_argument("--reason", default="")
    pend.set_defaults(run=cmd_pending)
    skills = sub.add_parser("skills", help="Skills: list, lock, unlock")
    skills_sub = skills.add_subparsers(dest="action", required=True)
    skills_sub.add_parser("list")
    for name in ("lock", "unlock"):
        skills_sub.add_parser(name).add_argument("name")
    skills.set_defaults(run=cmd_skills)
    ledger = sub.add_parser("ledger", help="Execuções e qualidade por skill: record, stats, warnings")
    ledger_sub = ledger.add_subparsers(dest="action", required=True)
    record = ledger_sub.add_parser("record")
    for name in ("skill", "task_type", "tier"):
        record.add_argument(name)
    record.add_argument("outcome", choices=["ok", "fail"])
    record.add_argument("source", choices=["correction", "test", "reviewer", "routine"])
    record.add_argument("--ref", default="")
    stats = ledger_sub.add_parser("stats")
    stats.add_argument("skill")
    stats.add_argument("tier")
    ledger_sub.add_parser("warnings").add_argument("skill", nargs="?", default=None)
    ledger.set_defaults(run=cmd_ledger)
    ladder = sub.add_parser("ladder", help="Escada de custo: review [skill], crystallize <skill> --file <script>")
    ladder_sub = ladder.add_subparsers(dest="action", required=True)
    ladder_sub.add_parser("review").add_argument("skill", nargs="?", default=None)
    crystal = ladder_sub.add_parser("crystallize")
    crystal.add_argument("skill")
    crystal.add_argument("--file", required=True)
    ladder.set_defaults(run=cmd_ladder)
    args = parser.parse_args(argv)
    return (getattr(args, "run", None) or cmd_serve)(args)


if __name__ == "__main__":
    raise SystemExit(main())
