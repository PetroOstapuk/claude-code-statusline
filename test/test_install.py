"""install.sh against a throwaway HOME."""
import json
import os
import subprocess
import unittest

from helpers import BASH, INSTALL, STATUSLINE, SandboxTestCase


class Install(SandboxTestCase):
    def setUp(self):
        super().setUp()
        self.claude = self.home / ".claude"
        self.target = self.claude / "statusline.sh"
        self.settings = self.claude / "settings.json"

    def run_install(self, *args, check=True):
        proc = subprocess.run([BASH, str(INSTALL), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, env=self.env())
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc

    def settings_json(self):
        return json.loads(self.settings.read_text())

    def config_text(self):
        return self.config.read_text() if self.config.exists() else ""

    def backups(self, path):
        return sorted(p.name for p in path.parent.glob(path.name + ".bak-*"))

    # ── fresh machine ───────────────────────────────────────────────────────
    def test_fresh_install_links_the_script_and_wires_settings(self):
        self.run_install()
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(os.readlink(self.target), str(STATUSLINE.resolve()))
        self.assertEqual(self.settings_json()["statusLine"], {
            "padding": 0, "refreshInterval": 10, "type": "command", "command": str(self.target)})
        self.assertTrue(self.config.exists())
        self.assertNotIn("ISSUE_URL=", self.config_text())

    def test_paths_are_shown_with_a_tilde(self):
        out = self.run_install().stdout
        self.assertIn("linked ~/.claude/statusline.sh -> ", out)
        self.assertNotIn("\\~", out)
        self.assertNotIn(str(self.home), out)

    def test_copy_mode(self):
        self.run_install("--copy")
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), STATUSLINE.read_bytes())
        self.assertTrue(os.access(self.target, os.X_OK))

    # ── issue tracker URL ───────────────────────────────────────────────────
    def test_issue_url_forms(self):
        cases = {
            "acme.atlassian.net": "https://acme.atlassian.net/browse/{key}",
            "https://acme.atlassian.net/jira/software/projects/ABC/boards/1":
                "https://acme.atlassian.net/browse/{key}",
            "https://acme.atlassian.net/browse/ABC-1": "https://acme.atlassian.net/browse/{key}",
            "https://jira.example.com/browse/ABC-7": "https://jira.example.com/browse/{key}",
            "https://example.com/jira/": "https://example.com/jira/browse/{key}",
            "https://linear.app/acme/issue/{key}": "https://linear.app/acme/issue/{key}",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                out = self.run_install("--issue-url", given).stdout
                self.assertIn(f"ISSUE_URL={expected}\n", self.config_text())
                self.assertEqual(self.config_text().count("ISSUE_URL="), 1)
                self.assertIn("ABC-123 -> " + expected.replace("{key}", "ABC-123"), out)

    def test_bad_issue_url_is_rejected(self):
        proc = self.run_install("--issue-url", 'https://a.example.com/"x', check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.settings.exists())

    def test_no_issue_url_removes_only_that_key(self):
        self.run_install("--issue-url", "acme.atlassian.net", "--strip-branch-prefix")
        with self.config.open("a") as f:
            f.write("ICON_MODEL=*\n")
        self.run_install("--no-issue-url")
        text = self.config_text()
        self.assertNotIn("ISSUE_URL=", text)
        self.assertIn("STRIP_BRANCH_PREFIX=1\n", text)
        self.assertIn("ICON_MODEL=*\n", text)

    def test_branch_prefix_toggle(self):
        self.run_install("--strip-branch-prefix")
        self.assertIn("STRIP_BRANCH_PREFIX=1\n", self.config_text())
        self.run_install("--keep-branch-prefix")
        self.assertIn("STRIP_BRANCH_PREFIX=0\n", self.config_text())
        self.assertEqual(self.config_text().count("STRIP_BRANCH_PREFIX"), 1)

    # ── existing setups ─────────────────────────────────────────────────────
    def test_existing_settings_are_kept_and_backed_up(self):
        self.claude.mkdir()
        self.settings.write_text(json.dumps({
            "model": "opus", "statusLine": {"type": "command", "command": "other-tool",
                                            "hideVimModeIndicator": True}}))
        self.run_install()
        data = self.settings_json()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["statusLine"]["command"], str(self.target))
        self.assertTrue(data["statusLine"]["hideVimModeIndicator"])
        [backup] = self.backups(self.settings)
        self.assertEqual(json.loads((self.claude / backup).read_text())["statusLine"]["command"],
                         "other-tool")

    def test_existing_script_is_backed_up(self):
        self.claude.mkdir()
        self.target.write_text("#!/bin/sh\necho mine\n")
        self.run_install()
        [backup] = self.backups(self.target)
        self.assertEqual((self.claude / backup).read_text(), "#!/bin/sh\necho mine\n")

    def test_rerun_is_idempotent(self):
        self.run_install("--issue-url", "acme.atlassian.net")
        out = self.run_install().stdout
        self.assertIn("already linked", out)
        self.assertIn("settings.json already uses it", out)
        self.assertEqual(self.backups(self.settings), [])
        self.assertEqual(self.config_text().count("ISSUE_URL="), 1)

    def test_invalid_settings_json_is_left_untouched(self):
        self.claude.mkdir()
        self.settings.write_text("{ not json")
        proc = self.run_install(check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.settings.read_text(), "{ not json")

    def test_symlinked_settings_stay_symlinked(self):
        self.claude.mkdir()
        real = self.tmp / "dotfiles-settings.json"
        real.write_text("{}")
        self.settings.symlink_to(real)
        self.run_install()
        self.assertTrue(self.settings.is_symlink())
        self.assertIn("statusLine", json.loads(real.read_text()))

    # ── uninstall ───────────────────────────────────────────────────────────
    def test_uninstall(self):
        self.run_install("--issue-url", "acme.atlassian.net")
        self.run_install("--uninstall")
        self.assertFalse(self.target.exists() or self.target.is_symlink())
        self.assertNotIn("statusLine", self.settings_json())
        self.assertIn("ISSUE_URL=", self.config_text())          # settings survive

    def test_uninstall_leaves_foreign_scripts_alone(self):
        self.claude.mkdir()
        self.target.write_text("#!/bin/sh\necho mine\n")
        self.settings.write_text('{"statusLine": {"type": "command", "command": "elsewhere"}}')
        self.run_install("--uninstall")
        self.assertEqual(self.target.read_text(), "#!/bin/sh\necho mine\n")
        self.assertEqual(self.settings_json()["statusLine"]["command"], "elsewhere")

    def test_help(self):
        self.assertIn("--issue-url URL", self.run_install("--help").stdout)
        self.assertNotEqual(self.run_install("--bogus", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
