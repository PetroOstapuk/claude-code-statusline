"""tools/usage-report.py against a handful of synthetic transcripts."""
import json
import subprocess
import sys
import unittest

from helpers import ROOT, SandboxTestCase

REPORT = ROOT / "tools" / "usage-report.py"
M = 1_000_000


def assistant(msg_id, ts, model, inp=0, w5=0, w1=0, read=0, out=0, think=0):
    return {"type": "assistant", "timestamp": ts, "requestId": "req_" + msg_id,
            "message": {"id": msg_id, "model": model, "content": [{"type": "text", "text": "secret"}],
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": w5 + w1,
                                  "cache_read_input_tokens": read, "output_tokens": out,
                                  "cache_creation": {"ephemeral_5m_input_tokens": w5,
                                                     "ephemeral_1h_input_tokens": w1},
                                  "output_tokens_details": {"thinking_tokens": think}}}}


def user(ts, text=None, tool_result=False):
    content = [{"type": "tool_result", "content": "x"}] if tool_result else text
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": content}}


class UsageReport(SandboxTestCase):
    def setUp(self):
        super().setUp()
        self.projects = self.tmp / "projects"
        main = self.projects / "-work-my-app" / "s1.jsonl"
        sub = self.projects / "-work-my-app" / "s1" / "subagents" / "agent-a.jsonl"
        sub.parent.mkdir(parents=True)
        first = assistant("m1", "2026-09-01T10:00:05Z", "claude-opus-5", inp=10_000, w1=M, out=100_000,
                          think=60_000)
        self.write(main, [
            user("2026-09-01T10:00:00Z", "a prompt that must never be printed"),
            first, first,                                            # one line per content block
            user("2026-09-01T10:00:06Z", tool_result=True),
            assistant("m2", "2026-09-01T10:00:30Z", "claude-opus-5", inp=5_000, w1=50_000, read=M,
                      out=50_000),
            user("2026-09-01T12:00:00Z", [{"type": "text", "text": "second prompt"}]),
            user("2026-09-01T12:00:01Z", "<local-command-stdout>ok</local-command-stdout>"),
            assistant("m3", "2026-09-01T12:00:10Z", "claude-opus-5", w1=1_100_000, out=20_000),
            assistant("m4", "2026-09-01T12:00:20Z", "<synthetic>", out=5),
            assistant("m5", "2026-09-01T12:00:30Z", "claude-future-9", inp=1_000, out=1_000),
        ])
        self.write(sub, [
            assistant("s1", "2026-09-01T10:00:10Z", "claude-haiku-4-5-20251001", inp=100_000,
                      w5=200_000, read=300_000, out=10_000),
        ])

    @staticmethod
    def write(path, entries):
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def run_report(self, *args):
        proc = subprocess.run([sys.executable, str(REPORT), "--projects", str(self.projects), *args],
                              capture_output=True, text=True, env=self.env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_numbers(self):
        r = json.loads(self.run_report("--json").stdout)
        self.assertEqual(r["sessions"], {"main": 1, "subagent_transcripts": 1, "with_subagents": 1})
        # opus-5: $5 in, $25 out, $10 1-hour write, $0.50 read; haiku 4.5: $1, $5, $1.25, $0.10
        m1 = 0.05 + 10 + 2.5
        m2 = 0.025 + 0.5 + 0.5 + 1.25
        m3 = 11 + 0.5
        s1 = 0.1 + 0.25 + 0.03 + 0.05
        self.assertAlmostEqual(r["total_cost"], m1 + m2 + m3 + s1, delta=0.01)
        self.assertEqual(r["tokens"]["out"], 100_000 + 50_000 + 20_000 + 1_000 + 10_000)  # m1 counted once
        self.assertEqual(r["unpriced"], {"claude-future-9": 1})
        self.assertEqual(r["cold_restarts"]["count"], 1)                    # 10:00:30 -> 12:00:10
        self.assertAlmostEqual(r["cold_restarts"]["cost"], 11.0)
        self.assertEqual([b["sessions"] for b in r["buckets"]], [1, 0, 0, 0, 0])   # two prompts
        self.assertEqual(r["prefix_median"], 10_000 + M)
        self.assertAlmostEqual(r["subagent_share"], round(s1 / (m1 + m2 + m3 + s1), 4))
        self.assertAlmostEqual(r["thinking_share_of_output"], round(60_000 / 181_000, 4))

    def test_text_report_never_contains_transcript_text(self):
        out = self.run_report().stdout
        self.assertIn("Where the spend goes", out)
        self.assertNotIn("must never be printed", out)
        self.assertNotIn("secret", out)

    def test_since(self):
        r = json.loads(self.run_report("--json", "--since", "2026-09-01").stdout)
        self.assertEqual(r["sessions"]["main"], 1)
        proc = subprocess.run([sys.executable, str(REPORT), "--projects", str(self.projects),
                               "--since", "2026-09-02"], capture_output=True, text=True, env=self.env())
        self.assertNotEqual(proc.returncode, 0)                             # nothing left to report
        self.assertIn("no Claude Code transcripts", proc.stderr)


if __name__ == "__main__":
    unittest.main()
