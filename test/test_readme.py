"""The README example is real output of the script, not a hand-made mock-up."""
import json
import re
import unittest

from helpers import ROOT, SandboxTestCase, strip


class ReadmeExample(SandboxTestCase):
    def test_example_block_matches_the_script(self):
        readme = (ROOT / "README.md").read_text()
        block = re.search(r"## What each part means\s+```\n(.*?)\n```", readme, re.S).group(1)
        payload = json.loads((ROOT / "tools" / "sample-payload.json").read_text())
        payload["workspace"]["current_dir"] = str(self.repo(branch="ABC-123-add-login"))
        self.write_config("ISSUE_URL=https://acme.atlassian.net/browse/{key}\n")
        line1, line2 = self.render(payload)
        self.assertEqual(block.split("\n"), [strip(line1), strip(line2)])


if __name__ == "__main__":
    unittest.main()
