#!/usr/bin/env python3
"""Renders the README picture: assets/statusline.svg.

Runs the real statusline.sh on made-up sessions in a throwaway repository,
then turns its ANSI output into SVG on a fixed character grid, so the picture
is exactly what the script prints and can be regenerated after any change:

    python3 tools/render-demo.py
"""
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "statusline.svg"
NOW = 1790000000

FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"
SIZE, CELL, LINE = 14, 8.43, 21          # font px, column width, row height
PAD_X, PAD_Y, GAP = 20, 16, 34

BG, FG, CAPTION = "#1d2026", "#d7dae0", "#8b93a3"
ANSI = {31: "#ef6f78", 32: "#8fcf6a", 33: "#e8c170", 35: "#c786e8", 36: "#4fc1cf"}

# The first session is tools/sample-payload.json, the same one the README
# example and tools/bench.sh use; its timestamps are relative to NOW.
SESSIONS = [
    ("An everyday session", json.loads((ROOT / "tools" / "sample-payload.json").read_text())),
    ("A long session near its limits", {
        "model": {"display_name": "Fable 5.1 (1M context)"},
        "context_window": {"used_percentage": 81},
        "effort": {"level": "max"},
        "fast_mode": True,
        "cost": {"total_cost_usd": 48.9, "total_lines_added": 1204, "total_lines_removed": 388,
                 "total_duration_ms": 19800000},
        "pr": {"number": 42, "review_state": "changes_requested"},
        "prompt_cache": {"warm": True, "expires_at": NOW + 170, "hit_ratio": 0.884},
        "rate_limits": {"five_hour": {"used_percentage": 92, "resets_at": NOW + 1500},
                        "seven_day": {"used_percentage": 74, "resets_at": NOW + 270000}},
    }),
]

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OSC8 = re.compile(r"\x1b\]8;;[^\x07]*\x07")


def render_ansi(payload, workdir, config):
    env = {"PATH": os.environ["PATH"], "HOME": str(workdir), "TZ": "UTC", "LC_TIME": "C",
           "LANG": "en_US.UTF-8", "COLUMNS": "200", "CLAUDE_STATUSLINE_TEST_NOW": str(NOW),
           "CLAUDE_STATUSLINE_CONFIG": str(config)}
    out = subprocess.run(["bash", str(ROOT / "statusline.sh")], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, check=True).stdout
    return out.rstrip("\n").split("\n")


def cells(line):
    """ANSI text -> [(column, char, style)], wide characters taking two columns."""
    style = {"fg": None, "bold": False, "dim": False, "underline": False}
    out, col, pos = [], 0, 0
    line = OSC8.sub("", line)
    while pos < len(line):
        m = SGR.match(line, pos)
        if m:
            codes = [int(c) for c in m.group(1).split(";") if c] or [0]
            i = 0
            while i < len(codes):
                c = codes[i]
                if c == 0:
                    style = {"fg": None, "bold": False, "dim": False, "underline": False}
                elif c == 1:
                    style = dict(style, bold=True)
                elif c == 2:
                    style = dict(style, dim=True)
                elif c == 4:
                    style = dict(style, underline=True)
                elif c in ANSI:
                    style = dict(style, fg=ANSI[c])
                elif c == 38 and codes[i + 1:i + 2] == [2]:
                    r, g, b = codes[i + 2:i + 5]
                    style = dict(style, fg=f"#{r:02x}{g:02x}{b:02x}")
                    i += 4
                i += 1
            pos = m.end()
            continue
        ch = line[pos]
        width = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        out.append((col, ch, dict(style)))
        col += width
        pos += 1
    return out, col


def text_attrs(style):
    attrs = [f'fill="{style["fg"] or FG}"']
    if style["bold"]:
        attrs.append('font-weight="700"')
    if style["dim"]:
        attrs.append('fill-opacity="0.6"')
    return " ".join(attrs)


def svg_line(line, y):
    parts, underlines = [], []
    run, run_style = [], None

    def flush():
        if run:
            xs = " ".join(f"{PAD_X + c * CELL:.1f}" for c, _ in run)
            text = html.escape("".join(ch for _, ch in run))
            parts.append(f'<text x="{xs}" y="{y}" {text_attrs(run_style)}>{text}</text>')

    grid, width = cells(line)
    for col, ch, style in grid:
        if style["underline"] and ch != " ":
            underlines.append((col, style["fg"] or FG))
        wide = unicodedata.east_asian_width(ch) in "WF"
        if ch == " " or wide or style != run_style:
            flush()
            run, run_style = [], style
        if ch == " ":
            continue
        if wide:                                   # emoji get their own element
            parts.append(f'<text x="{PAD_X + col * CELL:.1f}" y="{y}">{html.escape(ch)}</text>')
            continue
        run.append((col, ch))
    flush()
    for col, colour in underlines:
        x = PAD_X + col * CELL
        parts.append(f'<line x1="{x:.1f}" x2="{x + CELL:.1f}" y1="{y + 3}" y2="{y + 3}" '
                     f'stroke="{colour}" stroke-width="1"/>')
    return parts, width


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "my-app"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=demo", "-c", "user.email=demo@example.com",
               "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q", "-b", "ABC-123-add-login"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "init"], check=True)
        config = tmp / "config"
        config.write_text("ISSUE_URL=https://acme.atlassian.net/browse/{key}\n")

        blocks = []
        for caption, overrides in SESSIONS:
            payload = {"model": {"display_name": "Opus 5.5 (1M context)"}, "effort": {"level": "high"}}
            payload.update(overrides)
            payload["workspace"] = {"current_dir": str(repo)}
            blocks.append((caption, render_ansi(payload, tmp, config)))

    body, widest, y = [], 0, PAD_Y
    for caption, lines in blocks:
        y += 12
        body.append(f'<text x="{PAD_X}" y="{y}" fill="{CAPTION}" font-size="12">{html.escape(caption)}</text>')
        top = y + 10
        y = top + PAD_Y + SIZE
        rows = []
        for line in lines:
            parts, width = svg_line(line, y)
            rows.extend(parts)
            widest = max(widest, width)
            y += LINE
        box_h = y - top - LINE + PAD_Y
        body.append(("BOX", top, box_h))
        body.extend(rows)
        y = top + box_h + GAP

    width = int(PAD_X * 2 + widest * CELL)
    height = int(y - GAP + PAD_Y)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="{SIZE}">',
           f'<title>claude-code-statusline: two example status lines</title>']
    for item in body:
        if isinstance(item, tuple):
            _, top, box_h = item
            out.append(f'<rect x="4" y="{top}" width="{width - 8}" height="{box_h}" rx="10" fill="{BG}"/>')
        else:
            out.append(item)
    out.append("</svg>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({width}x{height})")


if __name__ == "__main__":
    main()
