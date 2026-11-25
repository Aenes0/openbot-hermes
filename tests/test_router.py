import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes_mcp.ledger import Ledger
from hermes_mcp.router import MIN_SUCCESS_RATE, Router
from hermes_mcp.skills import SkillStore
from hermes_mcp.tools import HermesToolbox, ToolError

from helpers import FakeOpenBot


class RouterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        self.fake = FakeOpenBot(self.dir / "openbot.db")
        self.ledger = Ledger(self.home, self.fake.path)
        self.skills = SkillStore(self.home / "skills")
        self.router = Router(self.ledger, self.skills)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("HERMES_MCP_PROMOTE_N", None)

    def tearDown(self):
        self._env.stop()
        self.fake.close()
        self._tmp.cleanup()

    def runs(self, tier, ok, fail=0, skill="a", task="resumo"):
        for _ in range(ok):
            self.ledger.record(skill, task, tier, "ok", "test")
        for _ in range(fail):
            self.ledger.record(skill, task, tier, "fail", "test")

    def test_picks_lowest_proven_tier(self):
        self.runs("strong", 10)
        self.runs("sonnet", 6)
        self.runs("haiku", 5)
        result = self.router.route_task("resumo")
        self.assertEqual(result["tier"], "haiku")
        self.assertEqual(result["evidence"]["n_required"], 5)
        self.assertEqual(result["evidence"]["per_tier"]["haiku"]["runs"], 5)
        self.assertIn("haiku", result["evidence"]["reason"])

    def test_script_tier_wins_when_proven(self):
        self.runs("script", 5)
        self.runs("haiku", 9)
        self.assertEqual(self.router.route_task("resumo")["tier"], "script")

    def test_no_evidence_keeps_current_tier(self):
        self.skills.apply({"action": "create", "name": "resumir", "category": "x", "description": "d",
                           "body": "b\n", "cost_tier": "sonnet"})
        self.assertEqual(self.router.route_task("resumo", skill="resumir")["tier"], "sonnet")
        self.assertEqual(self.router.route_task("resumo")["tier"], "strong")  # sem skill: o mais seguro
        self.assertEqual(self.router.route_task("resumo", skill="nao-existe")["tier"], "strong")
        self.runs("haiku", 4)  # abaixo de N
        self.assertEqual(self.router.route_task("resumo", skill="resumir")["tier"], "sonnet")

    def test_failure_rate_blocks_tier(self):
        self.runs("haiku", 8, fail=2)   # 80% < 90%
        self.runs("sonnet", 9, fail=1)  # 90% exatos: vale
        result = self.router.route_task("resumo")
        self.assertEqual(result["tier"], "sonnet")
        self.assertAlmostEqual(result["evidence"]["per_tier"]["haiku"]["rate"], 0.8)
        self.assertEqual(MIN_SUCCESS_RATE, 0.9)

    def test_n_configurable_by_env(self):
        self.runs("haiku", 2)
        self.assertEqual(self.router.route_task("resumo")["tier"], "strong")
        with mock.patch.dict(os.environ, {"HERMES_MCP_PROMOTE_N": "2"}):
            result = self.router.route_task("resumo")
        self.assertEqual(result["tier"], "haiku")
        self.assertEqual(result["evidence"]["n_required"], 2)
        self.assertEqual(Router(self.ledger, self.skills, n=1).route_task("resumo")["tier"], "haiku")

    def test_other_task_types_do_not_count(self):
        self.runs("haiku", 9, task="outra")
        self.assertEqual(self.router.route_task("resumo")["tier"], "strong")

    def test_register_route_task_tool(self):
        self.runs("haiku", 5)
        box = HermesToolbox(None, self.skills, None, None, agent_id="a")
        self.router.register(box)
        self.assertEqual(box.list_tools()[-1]["name"], "route_task")
        out = json.loads(box.call("route_task", {"task_type": "resumo"}))
        self.assertTrue(out["untrusted_content"])
        self.assertEqual(out["data"]["tier"], "haiku")
        with self.assertRaises(ToolError):
            box.call("route_task", {})

    def test_route_task_uses_ingested_openbot_data_when_available(self):
        box = HermesToolbox(None, self.skills, None, None, agent_id="a")
        self.router.register(box)
        self.fake.routine_run("run-1", "failed")
        for i in range(5):
            self.ledger.record("a", "resumo", "haiku", "ok", "routine", ref=f"run-{i}")
        self.ledger.record("a", "resumo", "haiku", "ok", "routine", ref="run-1")
        out = json.loads(box.call("route_task", {"task_type": "resumo"}))["data"]
        self.assertEqual(out["evidence"]["per_tier"]["haiku"]["fail"], 1)  # status do OpenBot ingerido na chamada

    def test_route_task_survives_missing_openbot_db(self):
        router = Router(Ledger(self.home, self.dir / "sumiu.db"), self.skills)
        box = HermesToolbox(None, self.skills, None, None, agent_id="a")
        router.register(box)
        out = json.loads(box.call("route_task", {"task_type": "resumo"}))["data"]
        self.assertEqual(out["tier"], "strong")
        self.assertIn("ingest_error", out["evidence"])


if __name__ == "__main__":
    unittest.main()
