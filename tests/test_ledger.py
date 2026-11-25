import os
import stat
import tempfile
import unittest
from pathlib import Path

from hermes_mcp.ledger import Ledger, LedgerError

from helpers import FakeOpenBot


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        self.fake = FakeOpenBot(self.dir / "openbot.db")
        self.ledger = Ledger(self.home, self.fake.path)

    def tearDown(self):
        self.fake.close()
        self._tmp.cleanup()

    def test_stats_counts_and_average_cost(self):
        for i, cost in enumerate((0.10, 0.20, 0.30)):
            self.fake.usage("agent-1", f"rec{i}", "s", f"turn{i}", cost)
            self.ledger.record("resumir", "resumo", "sonnet", "ok", "test", ref=f"turn{i}")
        self.fake.usage("agent-1", "rec9", "s", "turn9", 0.40)
        self.ledger.record("resumir", "resumo", "sonnet", "fail", "correction", ref="turn9")
        self.ledger.record("resumir", "resumo", "haiku", "ok", "test", ref="turnH")  # outro tier
        self.ledger.ingest()
        stats = self.ledger.stats("resumir", "sonnet")
        self.assertEqual((stats["runs"], stats["ok"], stats["fail"]), (4, 3, 1))
        self.assertAlmostEqual(stats["avg_cost_usd"], 0.25)
        self.assertEqual(self.ledger.stats("resumir", "haiku")["runs"], 1)
        self.assertEqual(self.ledger.stats("resumir", "strong"), {"runs": 0, "ok": 0, "fail": 0, "avg_cost_usd": 0.0})
        self.assertEqual(self.ledger.stats("outra", "sonnet")["runs"], 0)

    def test_correction_overrides_routine_success(self):
        self.ledger.record("s", "t", "haiku", "ok", "routine", ref="x")
        self.ledger.record("s", "t", "haiku", "fail", "correction", ref="x")
        stats = self.ledger.stats("s", "haiku")
        self.assertEqual((stats["runs"], stats["ok"], stats["fail"]), (1, 0, 1))

    def test_full_precedence_order(self):
        # correção > teste > revisor > rotina, em qualquer ordem de registro
        order = [("routine", "ok"), ("reviewer", "fail"), ("test", "ok"), ("correction", "fail")]
        for source, outcome in order:
            self.ledger.record("a", "t", "sonnet", outcome, source, ref="r1")
        self.assertEqual(self.ledger.stats("a", "sonnet")["fail"], 1)
        for source, outcome in reversed(order):
            self.ledger.record("b", "t", "sonnet", outcome, source, ref="r1")
        self.assertEqual(self.ledger.stats("b", "sonnet")["fail"], 1)
        self.ledger.record("c", "t", "sonnet", "fail", "routine", ref="r1")
        self.ledger.record("c", "t", "sonnet", "ok", "reviewer", ref="r1")
        self.assertEqual(self.ledger.stats("c", "sonnet")["ok"], 1)
        self.ledger.record("d", "t", "sonnet", "fail", "reviewer", ref="r1")
        self.ledger.record("d", "t", "sonnet", "ok", "test", ref="r1")
        self.assertEqual(self.ledger.stats("d", "sonnet")["ok"], 1)

    def test_same_source_twice_latest_wins(self):
        self.ledger.record("a", "t", "sonnet", "fail", "test", ref="r1")
        self.ledger.record("a", "t", "sonnet", "ok", "test", ref="r1")
        self.assertEqual(self.ledger.stats("a", "sonnet")["ok"], 1)

    def test_records_without_ref_are_separate_executions(self):
        self.ledger.record("a", "t", "sonnet", "ok", "test")
        self.ledger.record("a", "t", "sonnet", "ok", "test")
        self.assertEqual(self.ledger.stats("a", "sonnet")["runs"], 2)

    def test_routine_status_from_openbot_overrides_recorded_outcome(self):
        self.fake.routine_run("run-1", "failed")
        self.fake.routine_run("run-2", "succeeded")
        self.fake.routine_run("run-3", "running")
        self.ledger.record("r", "t", "haiku", "ok", "routine", ref="run-1")
        self.ledger.record("r", "t", "haiku", "fail", "routine", ref="run-2")
        self.ledger.record("r", "t", "haiku", "ok", "routine", ref="run-3")
        self.ledger.ingest()
        stats = self.ledger.stats("r", "haiku")
        self.assertEqual((stats["runs"], stats["ok"], stats["fail"]), (3, 2, 1))

    def test_ingest_is_idempotent(self):
        self.fake.usage("agent-1", "rec1", "s", "turn1", 0.1)
        self.fake.usage("agent-1", "rec2", "s", "turn2", 0.2)
        self.fake.routine_run("run-1", "succeeded")
        self.fake.routine_run("run-2", "failed")
        self.fake.routine_run("run-3", "running")  # não terminal: ignorado
        first = self.ledger.ingest()
        self.assertEqual(first, 4)
        self.assertEqual(self.ledger.ingest(), 0)
        self.fake.usage("agent-1", "rec3", "s", "turn3", 0.3)
        self.assertEqual(self.ledger.ingest(), 1)
        self.fake.db.execute("UPDATE projection_routine_runs SET status='failed' WHERE run_id='run-3'")
        self.fake.db.commit()
        self.assertEqual(self.ledger.ingest(), 1)
        self.assertEqual(self.ledger.ingest(), 0)

    def test_cost_comes_from_estimated_cost_usd(self):
        self.fake.usage("agent-1", "a", "s", "turn1", 0.5)
        self.fake.usage("agent-1", "b", "s", "turn1", 0.25)   # mesmo turno: soma
        self.fake.usage("agent-1", "c", "s", "turn2", None)    # sem estimativa: custo desconhecido
        self.ledger.record("x", "t", "sonnet", "ok", "test", ref="turn1")
        self.ledger.record("x", "t", "sonnet", "ok", "test", ref="turn2")
        self.ledger.ingest()
        stats = self.ledger.stats("x", "sonnet")
        self.assertEqual(stats["runs"], 2)
        self.assertAlmostEqual(stats["avg_cost_usd"], 0.75)  # média só das execuções com custo conhecido

    def test_tier_stats_across_skills_by_task_type(self):
        self.ledger.record("a", "resumo", "sonnet", "ok", "test")
        self.ledger.record("b", "resumo", "sonnet", "fail", "test")
        self.ledger.record("a", "resumo", "haiku", "ok", "test")
        self.ledger.record("a", "outro", "haiku", "ok", "test")
        stats = self.ledger.tier_stats("resumo")
        self.assertEqual(set(stats), {"sonnet", "haiku"})
        self.assertEqual(stats["sonnet"]["runs"], 2)
        self.assertEqual(stats["haiku"]["runs"], 1)

    def test_stats_after_mark_and_events(self):
        self.ledger.record("a", "t", "sonnet", "fail", "test")
        mark = self.ledger.mark_restored("a", "haiku", "sonnet", "falhou logo após descer")
        self.ledger.record("a", "t", "sonnet", "ok", "test")
        self.assertEqual(self.ledger.stats("a", "sonnet")["runs"], 2)
        self.assertEqual(self.ledger.stats("a", "sonnet", after_id=mark)["runs"], 1)
        self.assertEqual(self.ledger.last_mark("a"), mark)
        self.assertEqual(self.ledger.last_mark("outra"), 0)
        warnings = self.ledger.warnings("a")
        self.assertEqual(len(warnings), 1)
        self.assertIn("haiku", warnings[0]["message"])
        self.assertIn("falhou logo após descer", warnings[0]["message"])
        self.assertEqual(self.ledger.warnings("outra"), [])

    def test_validation(self):
        for args in (("", "t", "sonnet", "ok", "test"), ("a", "", "sonnet", "ok", "test"),
                     ("a", "t", "gratis", "ok", "test"), ("a", "t", "sonnet", "talvez", "test"),
                     ("a", "t", "sonnet", "ok", "palpite")):
            with self.assertRaises(LedgerError, msg=args):
                self.ledger.record(*args)

    def test_missing_openbot_db_on_ingest_is_clear_error(self):
        ledger = Ledger(self.home, self.dir / "sumiu.db")
        with self.assertRaises(Exception) as ctx:
            ledger.ingest()
        self.assertIn("não encontrado", str(ctx.exception))
        ledger.record("a", "t", "sonnet", "ok", "test")  # registrar não depende do banco do OpenBot
        self.assertEqual(ledger.stats("a", "sonnet")["runs"], 1)

    def test_ledger_file_is_private_and_openbot_untouched(self):
        self.ledger.record("a", "t", "sonnet", "ok", "test")
        self.assertEqual(stat.S_IMODE(os.stat(self.home / "ledger.db").st_mode), 0o600)
        before = self.fake.path.read_bytes()
        self.ledger.ingest()
        self.assertEqual(self.fake.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
