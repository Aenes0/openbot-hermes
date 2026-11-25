"""Escada de custo: desce um degrau (strong > sonnet > haiku > script) só com evidência; sobe sozinha após falha."""
from __future__ import annotations

from . import config
from .ledger import Ledger
from .pending import Pending, PendingError
from .skills import SkillError, SkillStore, TIERS

SCRIPT_PATH = "scripts/run.py"
TIER_LINE = "    cost_tier: {}"  # indentação do frontmatter escrito por skills.render (metadata > hermes)


class LadderError(Exception):
    """Revisão impossível ou recusada; a mensagem diz o motivo."""


class Ladder:
    def __init__(self, ledger: Ledger, skills: SkillStore, pending: Pending, n: int | None = None):
        self.ledger = ledger
        self.skills = skills
        self.pending = pending
        self._n = n

    def _required(self) -> int:
        return self._n if self._n is not None else config.promote_n()

    def _current(self, skill: str) -> str:
        for item in self.skills.list():
            if item["name"] == skill:
                return item["cost_tier"]
        raise LadderError(f"Skill não encontrada: {skill}")

    def _has_script(self, skill: str) -> bool:
        return SCRIPT_PATH in self.skills.files(skill)

    def _set_tier(self, skill: str, old: str, new: str) -> dict:
        return self.skills.apply({"action": "patch", "name": skill, "old": TIER_LINE.format(old),
                                  "new": TIER_LINE.format(new)})

    def _restore(self, skill: str, tier: str, reason: str) -> dict:
        above = TIERS[TIERS.index(tier) - 1]
        try:
            self._set_tier(skill, tier, above)
        except SkillError as err:
            raise LadderError(f"Não foi possível subir {skill} para {above}: {err}") from None
        self.ledger.mark_restored(skill, tier, above, reason)
        return {"skill": skill, "action": "restored", "from": tier, "to": above, "reason": reason}

    def review(self, skill: str) -> dict:
        """Uma revisão de custo da skill: subir (direto, com aviso), propor descer, pedir script, ou nada."""
        tier = self._current(skill)
        base = {"skill": skill, "tier": tier}
        if self.skills.is_locked(skill):
            return {**base, "action": "none", "reason": "skill travada pelo Alexandre: a escada não mexe nela"}
        if tier == "script" and not self._has_script(skill):
            return self._restore(skill, tier, f"o tier script exige {SCRIPT_PATH} e o arquivo não existe")
        stats = self.ledger.stats(skill, tier, after_id=self.ledger.last_mark(skill))
        if stats["fail"] > 0 and tier != TIERS[0]:
            return self._restore(skill, tier, f"{stats['fail']} falha(s) em {stats['runs']} execução(ões) no tier {tier}")
        required = self._required()
        if tier == TIERS[-1]:
            return {**base, "action": "none", "reason": "já está no último degrau (script)"}
        if stats["ok"] < required:
            return {**base, "action": "none",
                    "reason": f"{stats['ok']} de {required} execuções boas necessárias no tier {tier}"}
        lower = TIERS[TIERS.index(tier) + 1]
        if lower == "script" and not self._has_script(skill):
            return {**base, "action": "needs_script",
                    "reason": f"{stats['ok']} execuções boas em {tier}, mas falta {SCRIPT_PATH}: o Curador deve "
                              f"escrevê-lo e propor a cristalização (ladder crystallize)"}
        for open_proposal in self.pending.open_for(skill):
            if "cost_tier" in str(open_proposal["payload"].get("new", "")):
                return {**base, "action": "none", "proposal_id": open_proposal["id"],
                        "reason": "já existe uma proposta de tier pendente para esta skill"}
        reason = (f"{stats['ok']} execuções boas em {tier} (mínimo {required}), custo médio "
                  f"US$ {stats['avg_cost_usd']:.4f}; propõe descer para {lower}")
        try:
            pid = self.pending.propose("skill", {"action": "patch", "name": skill, "old": TIER_LINE.format(tier),
                                                 "new": TIER_LINE.format(lower), "reason": reason}, "ladder")
        except PendingError as err:
            raise LadderError(str(err)) from None
        return {**base, "action": "propose_descent", "from": tier, "to": lower, "proposal_id": pid, "reason": reason}

    def review_all(self) -> list[dict]:
        return [self.review(item["name"]) for item in self.skills.list()]

    def crystallize(self, skill: str, content: str) -> str:
        """Propõe criar scripts/run.py (determinístico, escrito pelo Curador). Devolve o id da proposta."""
        tier = self._current(skill)
        if self.skills.is_locked(skill):
            raise LadderError(f"A skill {skill} está travada; a escada não mexe nela.")
        if tier not in ("haiku", "script"):
            raise LadderError(f"Só se cristaliza em script uma skill no tier haiku ou script (agora: {tier}).")
        if not isinstance(content, str) or not content.strip():
            raise LadderError("O conteúdo do script não pode ser vazio.")
        try:
            return self.pending.propose("skill", {"action": "write_file", "name": skill, "path": SCRIPT_PATH,
                                                  "content": content,
                                                  "reason": "cristalizar a skill em script determinístico"}, "ladder")
        except PendingError as err:
            raise LadderError(str(err)) from None
