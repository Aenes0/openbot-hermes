"""Roteador: sugere o menor tier (mais barato) já provado para um tipo de tarefa."""
from __future__ import annotations

from . import config
from .ledger import Ledger
from .sessions import SessionsError
from .skills import SkillError, SkillStore, TIERS
from .tools import HermesToolbox, ToolError

MIN_SUCCESS_RATE = 0.9
CHEAPEST_FIRST = tuple(reversed(TIERS))  # script, haiku, sonnet, strong
SAFE_DEFAULT = "strong"


class Router:
    def __init__(self, ledger: Ledger, skills: SkillStore, n: int | None = None):
        self.ledger = ledger
        self.skills = skills
        self._n = n

    def _required(self) -> int:
        return self._n if self._n is not None else config.promote_n()

    def _current_tier(self, skill: str | None) -> str:
        if not skill:
            return SAFE_DEFAULT
        try:
            for item in self.skills.list():
                if item["name"] == skill:
                    return item["cost_tier"]
        except SkillError:
            pass
        return SAFE_DEFAULT

    def route_task(self, task_type: str, skill: str | None = None) -> dict:
        """Menor tier com >= N execuções e taxa de acerto >= 90%; sem evidência, o tier atual da skill."""
        required = self._required()
        stats = self.ledger.tier_stats(task_type)
        per_tier = {}
        for tier in CHEAPEST_FIRST:
            if tier in stats:
                entry = dict(stats[tier])
                entry["rate"] = entry["ok"] / entry["runs"] if entry["runs"] else 0.0
                per_tier[tier] = entry
        evidence = {"n_required": required, "min_success_rate": MIN_SUCCESS_RATE, "per_tier": per_tier}
        for tier in CHEAPEST_FIRST:
            entry = per_tier.get(tier)
            if entry and entry["runs"] >= required and entry["rate"] >= MIN_SUCCESS_RATE:
                evidence["reason"] = (f"{tier}: {entry['ok']}/{entry['runs']} execuções boas "
                                      f"({entry['rate']:.0%}), custo médio US$ {entry['avg_cost_usd']:.4f}")
                return {"tier": tier, "evidence": evidence}
        current = self._current_tier(skill)
        evidence["reason"] = f"sem evidência suficiente; mantém o tier atual ({current})"
        return {"tier": current, "evidence": evidence}

    def register(self, toolbox: HermesToolbox) -> None:
        """Expõe route_task(task_type, skill?) como ferramenta MCP (só leitura)."""
        def handler(args: dict) -> dict:
            task_type = args.get("task_type")
            if not isinstance(task_type, str) or not task_type.strip():
                raise ToolError("Parâmetro obrigatório ausente: task_type")
            ingest_error = ""
            try:
                self.ledger.ingest()
            except SessionsError as err:
                ingest_error = str(err)
            result = self.route_task(task_type.strip(), args.get("skill") or None)
            if ingest_error:
                result["evidence"]["ingest_error"] = ingest_error
            return result

        toolbox.register(
            "route_task",
            "Sugere o menor modelo (tier: script < haiku < sonnet < strong) já provado para um tipo de tarefa, com a "
            "evidência (execuções, taxa de acerto, custo). Sem evidência, devolve o tier atual da skill (ou strong).",
            {"type": "object", "properties": {"task_type": {"type": "string"}, "skill": {"type": "string"}},
             "required": ["task_type"]},
            handler)
