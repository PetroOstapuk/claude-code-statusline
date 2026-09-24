"""Shared helpers for the test suite (standard library only).

Every test runs the scripts against a throwaway HOME, so nothing on the
machine running the tests is read or changed.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSLINE = ROOT / "statusline.sh"
INSTALL = ROOT / "install.sh"

# Run the scripts with a specific bash, e.g. STATUSLINE_BASH=/bin/bash for the
# 3.2 that macOS ships.
BASH = os.environ.get("STATUSLINE_BASH", "bash")

NOW = 1790000000            # a fixed clock: 2026-09-21 14:13:20 UTC

OSC8 = re.compile(r"\x1b\]8;;([^\x07]*)\x07")
SGR = re.compile(r"\x1b\[[0-9;]*m")


def strip(text):
    """The text a terminal shows: colours and hyperlink wrappers removed."""
    return SGR.sub("", OSC8.sub("", text))


def columns(text):
    """Terminal columns taken by ANSI-free text (wide emoji count as two)."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def links(text):
    """URLs of every OSC 8 hyperlink that opens (the closing one is empty)."""
    return [url for url in OSC8.findall(text) if url]


def full_payload(cwd, **overrides):
    payload = {
        "model": {"display_name": "Opus 5.5 (1M context)"},
        "workspace": {"current_dir": str(cwd)},
        "context_window": {"used_percentage": 47.4},
        "effort": {"level": "high"},
        "cost": {"total_cost_usd": 26.456, "total_lines_added": 156,
                 "total_lines_removed": 23, "total_duration_ms": 160800000},
        "pr": {"number": 12, "review_state": "approved"},
        "prompt_cache": {"warm": True, "expires_at": NOW + 2850, "hit_ratio": 0.923},
        "fast_mode": True,
        "thinking": {"enabled": False},
        "rate_limits": {
            "five_hour": {"used_percentage": 10, "resets_at": NOW + 7860},
            "seven_day": {"used_percentage": 73.2, "resets_at": NOW + 270000},
        },
    }
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


class SandboxTestCase(unittest.TestCase):
    """A temporary HOME with helpers to make git repos and run the scripts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-statusline-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.home / ".config" / "claude-statusline" / "config"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "TZ": "UTC",
            "LANG": "en_US.UTF-8",
            "LC_TIME": "C",
            "COLUMNS": "200",
            "CLAUDE_STATUSLINE_TEST_NOW": str(NOW),
        }
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def git(self, repo, *args):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, env=self.env())

    def repo(self, name="my-app", branch="main", commit=True):
        path = self.tmp / name
        path.mkdir(parents=True)
        self.git(path, "init", "-q", "-b", "main")
        if commit:
            self.git(path, "commit", "-q", "--allow-empty", "-m", "init")
            if branch != "main":
                self.git(path, "checkout", "-q", "-b", branch)
        return path

    def write_config(self, text):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(text)

    def render(self, payload, **env):
        """Runs statusline.sh; returns (raw line 1, raw line 2)."""
        data = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run([BASH, str(STATUSLINE)], input=data, capture_output=True,
                              text=True, env=self.env(**env))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "", "the status line must not write to stderr")
        lines = proc.stdout.split("\n")
        self.assertEqual(len(lines), 3, repr(proc.stdout))    # two lines + final newline
        self.assertEqual(lines[2], "")
        return lines[0], lines[1]
