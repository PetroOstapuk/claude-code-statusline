"""Repository hygiene: no stray links, no personal paths.

Every URL in the repository must point at a host on the allowlist below.
Real tracker addresses belong in each user's own config file, never here, so
examples use acme.atlassian.net and example.com. Adding a host to the list is
a deliberate, reviewable act.
"""
import re
import subprocess
import unittest

from helpers import ROOT

ALLOWED_HOSTS = {
    "github.com", "raw.githubusercontent.com",
    "code.claude.com", "platform.claude.com", "support.claude.com", "claude.com",
    "jqlang.org", "starship.rs", "en.wikipedia.org", "www.shellcheck.net",
    "acme.atlassian.net", "linear.app", "img.shields.io",
    "www.w3.org",                                   # the SVG namespace, not a link
}
ALLOWED_SUFFIXES = (".example.com",)
EXAMPLE_HOSTS = {"example.com"}

URL = re.compile(r"https?://([A-Za-z0-9.-]+)")
HOME_PATH = re.compile(r"/(Users|home)/(?!username\b|user\b|you\b)[A-Za-z][\w.-]*")


def tracked_files():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard"], check=True, capture_output=True).stdout
        names = [n for n in out.decode().split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                 if p.is_file() and ".git" not in p.parts]
    return [ROOT / n for n in names]


class Hygiene(unittest.TestCase):
    def texts(self):
        for path in tracked_files():
            if not path.is_file():
                continue
            try:
                yield path, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

    def test_every_link_points_at_an_allowed_host(self):
        stray = []
        for path, text in self.texts():
            for host in URL.findall(text):
                host = host.lower().rstrip(".")
                if host in ALLOWED_HOSTS or host in EXAMPLE_HOSTS or host.endswith(ALLOWED_SUFFIXES):
                    continue
                stray.append(f"{path.relative_to(ROOT)}: {host}")
        self.assertEqual(stray, [], "hosts outside the allowlist")

    def test_no_personal_home_paths(self):
        found = []
        for path, text in self.texts():
            for match in HOME_PATH.finditer(text):
                found.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        self.assertEqual(found, [])

    def test_scripts_are_executable(self):
        for name in ("statusline.sh", "install.sh", "tools/bench.sh"):
            path = ROOT / name
            if path.exists():
                with self.subTest(name=name):
                    self.assertTrue(path.stat().st_mode & 0o111, f"{name} is not executable")


if __name__ == "__main__":
    unittest.main()
