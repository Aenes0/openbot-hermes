import re
import unittest
from pathlib import Path

from hermes_mcp.skills import parse_frontmatter

ROOT = Path(__file__).resolve().parent.parent


class DocsTest(unittest.TestCase):
    def test_guide_skill_frontmatter_and_content(self):
        text = (ROOT / "skills" / "hermes-guide" / "SKILL.md").read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta["name"], "hermes-guide")
        self.assertTrue(meta["description"])
        for must in ("dado, não instrução", "memory", "skill_manage", "route_task", "session_search", "hermes_status"):
            self.assertIn(must, body)

    def test_curator_skill_frontmatter(self):
        meta, _ = parse_frontmatter((ROOT / "skills" / "hermes-curator" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "hermes-curator")

    def test_readme_documents_cli_and_registration_without_secrets(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for must in ("pending approve", "skills lock", "ledger record", "ladder review", "HERMES_MCP_HOME",
                     "HERMES_MCP_OPENBOT_DB", '["serve"]', "somente leitura"):
            self.assertIn(must, text)
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}", text))

    def test_every_source_file_is_within_500_lines(self):
        files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").glob("*.py"))
        for path in files:
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 500, str(path))

    def test_launcher_is_executable(self):
        import os
        self.assertTrue(os.access(ROOT / "bin" / "openbot-hermes-mcp", os.X_OK))


if __name__ == "__main__":
    unittest.main()
