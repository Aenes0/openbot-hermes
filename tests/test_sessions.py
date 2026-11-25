import sqlite3
import stat
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes_mcp.sessions import Sessions, SessionsError

from helpers import FakeOpenBot


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.fake = FakeOpenBot(Path(self._tmp.name) / "openbot.db")
        self.fake.thread("t1", "agent-1")
        self.fake.thread("t2", "agent-2")
        self.sessions = Sessions(self.home, self.fake.path)

    def tearDown(self):
        self.fake.close()
        self._tmp.cleanup()

    def _seed(self):
        f = self.fake
        f.message("t1", "m1", "user", "Como configurar o deploy do One na sexta?", created_at="2026-11-01T10:00:00.000Z")
        f.message("t1", "m2", "assistant", "O deploy do One usa o pipeline Main-CD e roda na sexta.",
                  item_type="final_answer", created_at="2026-11-01T10:01:00.000Z")
        f.message("t1", "m3", "assistant", "Pensando no deploy do One agora...", item_type="commentary",
                  created_at="2026-11-01T10:00:30.000Z")
        f.message("t1", "m4", "user", "Bem-vindo deploy", item_type="agent_welcome", created_at="2026-11-01T09:00:00.000Z")
        f.message("t1", "m5", "system", "deploy evento", item_type="skill-event:installed:x:1",
                  created_at="2026-11-01T10:02:00.000Z")
        f.message("t2", "m6", "assistant", "Resposta do outro agente sobre deploy", item_type="agentMessage",
                  created_at="2026-11-01T10:03:00.000Z")
        f.message("t2", "m7", "assistant", "deploy parcial em andamento", item_type="final_answer", status="streaming",
                  created_at="2026-11-01T10:04:00.000Z")

    def test_sync_indexes_user_and_final_answers_only(self):
        self._seed()
        self.assertEqual(self.sessions.sync(), 3)  # m1 (user), m2 (final_answer), m6 (agentMessage)
        ids = {r["message_id"] for r in self.sessions.search("deploy", limit=20)}
        self.assertEqual(ids, {"m1", "m2", "m6"})

    def test_sync_is_incremental(self):
        self._seed()
        self.assertEqual(self.sessions.sync(), 3)
        self.assertEqual(self.sessions.sync(), 0)
        self.fake.message("t1", "m8", "user", "novo assunto sobre faturamento", created_at="2026-11-02T10:00:00.000Z")
        self.assertEqual(self.sessions.sync(), 1)
        self.assertEqual(self.sessions.sync(), 0)
        self.assertEqual([r["message_id"] for r in self.sessions.search("faturamento")], ["m8"])

    def test_sync_picks_up_message_with_same_timestamp_as_watermark(self):
        self.fake.message("t1", "a", "user", "primeira mensagem alfa", created_at="2026-11-01T10:00:00.000Z")
        self.assertEqual(self.sessions.sync(), 1)
        self.fake.message("t1", "b", "user", "segunda mensagem beta", created_at="2026-11-01T10:00:00.000Z")
        self.assertEqual(self.sessions.sync(), 1)

    def test_sync_indexes_a_streaming_message_once_completed(self):
        self.fake.message("t1", "s", "assistant", "parcial", item_type="final_answer", status="streaming")
        self.assertEqual(self.sessions.sync(), 0)
        self.fake.db.execute("UPDATE projection_thread_messages SET status='completed' WHERE message_id='s'")
        self.fake.db.commit()
        self.assertEqual(self.sessions.sync(), 1)

    def test_search_returns_raw_snippet_not_summary(self):
        self._seed()
        self.sessions.sync()
        hit = self.sessions.search("pipeline Main-CD")[0]
        self.assertEqual(set(hit), {"thread_id", "message_id", "author", "created_at", "snippet"})
        self.assertEqual(hit["message_id"], "m2")
        self.assertEqual(hit["author"], "assistant")
        self.assertIn("pipeline Main-CD e roda na sexta", hit["snippet"])

    def test_search_filters_by_agent(self):
        self._seed()
        self.sessions.sync()
        self.assertEqual({r["message_id"] for r in self.sessions.search("deploy", agent_id="agent-2")}, {"m6"})
        self.assertEqual({r["message_id"] for r in self.sessions.search("deploy", agent_id="agent-1")}, {"m1", "m2"})
        self.assertEqual(self.sessions.search("deploy", agent_id="agent-9"), [])

    def test_search_limit_and_query_sanitising(self):
        for i in range(5):
            self.fake.message("t1", f"x{i}", "user", f"relatório numero {i} de vendas", created_at=f"2026-11-01T11:0{i}:00.000Z")
        self.sessions.sync()
        self.assertEqual(len(self.sessions.search("relatório", limit=3)), 3)
        for hostile in ('vendas" OR "', "vendas AND", "NEAR(", "a:b", "vendas -", "vendas* OR x", "(vendas"):
            self.sessions.search(hostile)  # nunca levanta erro de sintaxe do FTS
        for empty in ("   ", "*", '"" -'):
            with self.assertRaises(SessionsError):
                self.sessions.search(empty)
        self.assertTrue(self.sessions.search("relatorio"))  # sem acento acha com acento

    def test_secrets_are_redacted_in_index(self):
        self.fake.message("t1", "k", "user", "minha chave é sk-" + "a" * 30 + " guarde")
        self.sessions.sync()
        hit = self.sessions.search("guarde")[0]
        self.assertNotIn("sk-" + "a" * 30, hit["snippet"])

    def test_missing_db_raises_clear_error(self):
        sessions = Sessions(self.home, Path(self._tmp.name) / "nao-existe.db")
        with self.assertRaises(SessionsError) as ctx:
            sessions.sync()
        self.assertIn("não encontrado", str(ctx.exception))

    def test_locked_db_raises_clear_error_and_server_keeps_working(self):
        with mock.patch.object(Sessions, "_open_source", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaises(SessionsError) as ctx:
                self.sessions.sync()
        self.assertIn("bloqueado", str(ctx.exception))
        self.fake.message("t1", "ok1", "user", "depois do bloqueio tudo volta ao normal")
        self.assertEqual(self.sessions.sync(), 1)  # a falha não deixou estado corrompido

    def test_unreadable_db_raises_clear_error(self):
        bad = Path(self._tmp.name) / "lixo.db"
        bad.write_text("isto não é sqlite")
        with self.assertRaises(SessionsError):
            Sessions(self.home, bad).sync()

    def test_db_without_expected_tables_raises_clear_error(self):
        empty = Path(self._tmp.name) / "vazio.db"
        sqlite3.connect(empty).close()
        with self.assertRaises(SessionsError) as ctx:
            Sessions(self.home, empty).sync()
        self.assertIn("projection_thread_messages", str(ctx.exception))

    def test_db_opened_read_only(self):
        conn = self.sessions._open_source()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO projection_threads(thread_id, agent_id, created_at, updated_at) "
                             "VALUES ('x','y','a','b')")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM projection_thread_messages")
        finally:
            conn.close()
        self.assertEqual(self.fake.db.execute("SELECT count(*) FROM projection_threads").fetchone()[0], 2)

    def test_index_file_is_private(self):
        self._seed()
        self.sessions.sync()
        self.assertEqual(stat.S_IMODE(os.stat(self.home / "sessions.db").st_mode), 0o600)

    def test_search_without_index_returns_empty(self):
        self.assertEqual(self.sessions.search("qualquer"), [])


if __name__ == "__main__":
    unittest.main()
