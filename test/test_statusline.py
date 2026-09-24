"""Behaviour of statusline.sh, one payload shape per test."""
import time
import unittest

from helpers import NOW, SandboxTestCase, columns, full_payload, links, strip


def utc(fmt, epoch):
    return time.strftime(fmt, time.gmtime(epoch))


class FullPayload(SandboxTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.repo(branch="ABC-123-add-login")

    def test_line_one(self):
        line1, _ = self.render(full_payload(self.app))
        self.assertEqual(strip(line1),
                         "my-app 🌱 ABC-123-add-login ✓ │ 🟢 █████████░░░░░░░░░░░ 47% │ "
                         "🚀 Opus 5.5 (1M context) ·high ⚡ 💤")

    def test_line_two(self):
        _, line2 = self.render(full_payload(self.app))
        self.assertEqual(strip(line2),
                         "$26.46 ($0.59/h) │ +156 -23 │ ◷ 44h 40m │ ☕ 47m 92% │ "
                         f"5h █░░░░░ 10% ↻2h11m ({utc('%H:%M', NOW + 7860)}) │ "
                         f"7d ████░░ 73% ↻3d3h ({utc('%a %H:%M', NOW + 270000)})")

    def test_fits_below_the_compact_threshold(self):
        line1, line2 = self.render(full_payload(self.app))
        self.assertLess(columns(strip(line1)), 120)
        self.assertLess(columns(strip(line2)), 120)

    def test_compact_layout_on_a_narrow_terminal(self):
        line1, line2 = self.render(full_payload(self.app), COLUMNS=100)
        self.assertEqual(strip(line1),
                         "my-app 🌱 ABC-123-add-login ✓ │ 🟢 █████░░░░░ 47% │ 🚀 Opus 5.5 ·high ⚡ 💤")
        self.assertEqual(strip(line2),
                         f"$26.46 │ +156 -23 │ ☕ 47m │ 5h 10% ↻2h11m ({utc('%H:%M', NOW + 7860)}) │ "
                         f"7d 73% ↻3d3h ({utc('%a %H:%M', NOW + 270000)})")
        self.assertLess(columns(strip(line1)), 100)
        self.assertLess(columns(strip(line2)), 100)

    def test_compact_can_be_turned_off(self):
        self.write_config("COMPACT_BELOW=0\n")
        _, line2 = self.render(full_payload(self.app), COLUMNS=80)
        self.assertIn("($0.59/h)", strip(line2))


class ContextLight(SandboxTestCase):
    def light(self, pct):
        line1, _ = self.render(full_payload(self.repo(name=f"r{pct}"),
                                            context_window={"used_percentage": pct}))
        return strip(line1).split(" │ ")[1].split(" ")[0]

    def test_thresholds(self):
        self.assertEqual(self.light(0), "🟢")
        self.assertEqual(self.light(49), "🟢")
        self.assertEqual(self.light(50), "🟡")
        self.assertEqual(self.light(70), "🟠")
        self.assertEqual(self.light(90), "🔴")
        self.assertEqual(self.light(100), "🔴")

    def test_no_reading_yet_is_neutral(self):
        line1, _ = self.render(full_payload(self.repo(), context_window=None))
        self.assertIn("⚪ ░░░░░░░░░░░░░░░░░░░░ --%", strip(line1))

    def test_bar_never_overflows(self):
        line1, _ = self.render(full_payload(self.repo(), context_window={"used_percentage": 250}))
        self.assertIn("█" * 20 + " 250%", strip(line1))


class IssueKeys(SandboxTestCase):
    def line1(self, branch, config=None, **env):
        if config is not None:
            self.write_config(config)
        repo = self.repo(branch=branch)
        line1, _ = self.render(full_payload(repo), **env)
        return line1

    def test_no_link_without_a_tracker(self):
        raw = self.line1("ABC-123-add-login")
        self.assertEqual(links(raw), [])
        self.assertIn("\x1b[1m\x1b[38;2;87;157;255mABC-123\x1b[0m", raw)   # blue, not underlined
        self.assertNotIn("\x1b[4m", raw)

    def test_link_with_a_template(self):
        raw = self.line1("ABC-123-add-login", "ISSUE_URL=https://acme.atlassian.net/browse/{key}\n")
        self.assertEqual(links(raw), ["https://acme.atlassian.net/browse/ABC-123"])
        self.assertEqual(raw.count("\x1b]8;;\x07"), 1)                    # one closing sequence
        self.assertIn("\x1b[4m\x1b]8;;", raw)                              # underlined link
        self.assertIn("ABC-123-add-login", strip(raw))

    def test_url_without_placeholder_gets_the_key_appended(self):
        raw = self.line1("ABC-123-x", "ISSUE_URL = https://jira.example.com/browse/ \n")
        self.assertEqual(links(raw), ["https://jira.example.com/browse/ABC-123"])

    def test_other_trackers_via_placeholder(self):
        raw = self.line1("ENG-42-fix", "ISSUE_URL=https://linear.app/acme/issue/{key}\n")
        self.assertEqual(links(raw), ["https://linear.app/acme/issue/ENG-42"])

    def test_unsafe_urls_are_ignored(self):
        for bad in ["javascript:alert(1)", "https://a.example.com/x y/{key}",
                    'https://a.example.com/"{key}', "ftp://a.example.com/{key}"]:
            with self.subTest(bad=bad):
                self.write_config(f"ISSUE_URL={bad}\n")
                raw, _ = self.render(full_payload(self.repo(name=f"r{abs(hash(bad))}",
                                                            branch="ABC-1-x")))
                self.assertEqual(links(raw), [])

    def test_control_bytes_in_the_config_cannot_break_the_link(self):
        raw = self.line1("ABC-9-x", "ISSUE_URL=https://acme.atlassian.net/browse/\x07\x1b[2J{key}\n")
        self.assertEqual(links(raw), ["https://acme.atlassian.net/browse/[2JABC-9"])
        self.assertNotIn("\x1b[2J", raw)

    def test_two_capitals_are_not_a_key(self):
        raw = self.line1("release/141-RC-2", "ISSUE_URL=https://acme.atlassian.net/browse/{key}\n")
        self.assertEqual(links(raw), [])
        self.assertIn("🌱 release/141-RC-2", strip(raw))

    def test_prefix_kept_by_default(self):
        self.assertIn("🌱 abc_ABC-123-add-login", strip(self.line1("abc_ABC-123-add-login")))

    def test_prefix_stripped_when_asked(self):
        config = "STRIP_BRANCH_PREFIX=1\n"
        self.assertIn("🌱 ABC-123-add-login", strip(self.line1("abc_ABC-123-add-login", config)))

    def test_short_handle_stripped_without_a_key(self):
        self.assertIn("🌱 fix-typo", strip(self.line1("abc_fix-typo", "STRIP_BRANCH_PREFIX=1\n")))

    def test_words_are_not_handles(self):
        self.assertIn("🌱 feature_dark-mode",
                      strip(self.line1("feature_dark-mode", "STRIP_BRANCH_PREFIX=1\n")))


class Git(SandboxTestCase):
    def test_detached_head_shows_the_short_sha(self):
        repo = self.repo()
        self.git(repo, "checkout", "-q", "--detach")
        line1, _ = self.render(full_payload(repo))
        self.assertRegex(strip(line1), r"^my-app 🌱 [0-9a-f]{7,} ")

    def test_repository_without_commits_shows_its_branch(self):
        repo = self.repo(commit=False)
        line1, _ = self.render(full_payload(repo))
        self.assertTrue(strip(line1).startswith("my-app 🌱 main "), strip(line1))

    def test_outside_a_repository(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        line1, _ = self.render(full_payload(plain))
        self.assertTrue(strip(line1).startswith("🟢 "), strip(line1))

    def test_escape_bytes_in_a_directory_name_are_dropped(self):
        repo = self.repo(name="ev\x1b[2Jil")
        line1, _ = self.render(full_payload(repo))
        self.assertNotIn("\x1b[2J", line1)
        self.assertTrue(strip(line1).startswith("ev[2Jil 🌱 main "), strip(line1))


class PullRequest(SandboxTestCase):
    def glyph(self, pr):
        line1, _ = self.render(full_payload(self.repo(name=f"r{len(str(pr))}{abs(hash(str(pr)))}"), pr=pr))
        return strip(line1).split(" │ ")[0].split(" ")[-1]

    def test_review_states(self):
        self.assertEqual(self.glyph({"number": 1, "review_state": "approved"}), "✓")
        self.assertEqual(self.glyph({"number": 1, "review_state": "changes_requested"}), "✗")
        self.assertEqual(self.glyph({"number": 1, "review_state": "draft"}), "◌")
        self.assertEqual(self.glyph({"number": 1, "review_state": "pending"}), "·")
        self.assertEqual(self.glyph({"number": 1}), "·")

    def test_no_pull_request(self):
        line1, _ = self.render(full_payload(self.repo(), pr=None))
        self.assertTrue(strip(line1).startswith("my-app 🌱 main │"), strip(line1))


class Flags(SandboxTestCase):
    def test_defaults_add_no_markers(self):
        line1, _ = self.render(full_payload(self.repo(), effort=None, fast_mode=False,
                                            thinking={"enabled": True}))
        self.assertTrue(strip(line1).endswith("🚀 Opus 5.5 (1M context)"), strip(line1))


class PromptCache(SandboxTestCase):
    def cache(self, cache, **env):
        _, line2 = self.render(full_payload(self.repo(), prompt_cache=cache), **env)
        return strip(line2).split(" │ ")[3]

    def test_warm(self):
        self.assertEqual(self.cache({"warm": True, "expires_at": NOW + 600, "hit_ratio": 0.5}), "☕ 10m 50%")

    def test_colour_follows_the_time_left(self):
        _, raw = self.render(full_payload(self.repo(), prompt_cache={"warm": True, "expires_at": NOW + 120}))
        self.assertIn("\x1b[31m☕ 2m", raw)

    def test_cold(self):
        self.assertEqual(self.cache({"warm": False, "expires_at": None, "hit_ratio": 0.97}), "🧊 cold 97%")

    def test_absent(self):
        _, line2 = self.render(full_payload(self.repo(), prompt_cache=None))
        self.assertNotIn("☕", line2)
        self.assertNotIn("🧊", line2)


class RateLimits(SandboxTestCase):
    def limits(self, rate_limits):
        _, line2 = self.render(full_payload(self.repo(), rate_limits=rate_limits))
        return strip(line2).split(" │ ")[4:]

    def test_absent_for_api_key_sessions(self):
        self.assertEqual(self.limits(None), [])

    def test_reset_clock_formats(self):
        got = self.limits({
            "five_hour": {"used_percentage": 1, "resets_at": NOW + 11 * 3600},     # after midnight
            "seven_day": {"used_percentage": 2, "resets_at": NOW + 8 * 86400},     # beyond six days
        })
        self.assertEqual(got, [f"5h ░░░░░░ 1% ↻11h0m ({utc('%a %H:%M', NOW + 11 * 3600)})",
                               f"7d ░░░░░░ 2% ↻8d0h ({utc('%d.%m %H:%M', NOW + 8 * 86400)})"])

    def test_non_numeric_reset_is_skipped(self):
        self.assertEqual(self.limits({"five_hour": {"used_percentage": 5, "resets_at": "soon"}}),
                         ["5h ░░░░░░ 5%"])

    def test_more_than_two_windows_drop_the_bars(self):
        got = self.limits({
            "five_hour": {"used_percentage": 5},
            "seven_day": {"used_percentage": 6},
            "spend_limit": {"used_percentage": 112},
        })
        self.assertEqual(got, ["5h 5%", "7d 6%", "spend 112%"])

    def test_unknown_windows_keep_a_safe_name(self):
        self.assertEqual(self.limits({"weird|;\x1bkey": {"used_percentage": 3}}), ["weirdkey ░░░░░░ 3%"])


class Robustness(SandboxTestCase):
    def test_broken_json(self):
        line1, line2 = self.render("{not json")
        self.assertEqual(strip(line1), "⚪ ░░░░░░░░░░░░░░░░░░░░ --% │ 🚀 ?")
        self.assertEqual(strip(line2), "$0.00 │ +0 -0 │ ◷ 0m 0s")

    def test_empty_input(self):
        line1, _ = self.render("")
        self.assertEqual(strip(line1), "⚪ ░░░░░░░░░░░░░░░░░░░░ --% │ 🚀 ?")

    def test_wrong_types_do_not_leak_errors(self):
        line1, line2 = self.render(full_payload(
            self.repo(), context_window={"used_percentage": "lots"},
            cost={"total_cost_usd": "free", "total_duration_ms": "long"},
            model={"display_name": "Evil\x1b[2J"}))
        self.assertIn("--%", strip(line1))
        self.assertNotIn("\x1b[2J", line1)
        self.assertTrue(strip(line2).startswith("$0.00 │"), strip(line2))

    def test_burn_rate_waits_for_the_first_minute(self):
        _, line2 = self.render(full_payload(self.repo(), cost={
            "total_cost_usd": 0.4, "total_lines_added": 0, "total_lines_removed": 0,
            "total_duration_ms": 30000}))
        self.assertTrue(strip(line2).startswith("$0.40 │ +0 -0 │ ◷ 0m 30s"), strip(line2))


class Config(SandboxTestCase):
    def test_icons_and_widths_are_configurable(self):
        self.write_config("# a comment\r\nICON_BRANCH=\r\nICON_MODEL=*\nCTX_BAR_WIDTH=5\n"
                          "LIMIT_BAR_WIDTH=abc\n")
        line1, line2 = self.render(full_payload(self.repo()))
        self.assertIn(" main", strip(line1))
        self.assertIn("🟢 ██░░░ 47%", strip(line1))
        self.assertIn("* Opus 5.5", strip(line1))
        self.assertIn("5h █░░░░░ 10%", strip(line2))      # invalid width ignored

    def test_config_path_can_be_overridden(self):
        other = self.tmp / "elsewhere.conf"
        other.write_text("ICON_BRANCH=@\n")
        line1, _ = self.render(full_payload(self.repo()), CLAUDE_STATUSLINE_CONFIG=other)
        self.assertIn("@ main", strip(line1))

    def test_the_file_is_never_executed(self):
        marker = self.tmp / "pwned"
        self.write_config(f"$(touch {marker})\nISSUE_URL=$(touch {marker})\n`touch {marker}`\n")
        self.render(full_payload(self.repo()))
        self.assertFalse(marker.exists())


class Debug(SandboxTestCase):
    def test_sentinel_dumps_the_payload(self):
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / ".statusline-debug").touch()
        self.render('{"model":{"display_name":"X"}}')
        self.assertEqual((claude / ".statusline-last.json").read_text(), '{"model":{"display_name":"X"}}')


if __name__ == "__main__":
    unittest.main()
