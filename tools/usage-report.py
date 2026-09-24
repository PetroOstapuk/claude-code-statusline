#!/usr/bin/env python3
"""Where your Claude Code tokens go: a local, read-only usage report.

Claude Code keeps a transcript of every session under ~/.claude/projects.
This script reads only the token counters stored with each API response,
re-prices them at list prices from prices.json, and prints the shares that
decide what a session costs. No prompt or response text makes it into the
report, and nothing leaves your machine.

    python3 tools/usage-report.py                  # everything still on disk
    python3 tools/usage-report.py --since 2026-09-01
    python3 tools/usage-report.py --json           # machine-readable

The dollar figures are an API-price equivalent, the same way Claude Code
estimates cost.total_cost_usd. On a Claude subscription you spend plan usage,
not dollars, so read the shares rather than the totals.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUCKETS = [(1, 5), (6, 15), (16, 30), (31, 60), (61, None)]
DATED_SNAPSHOT = re.compile(r"-\d{8}$")
NOT_PROMPTS = ("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
               "[Request interrupted")


# ── Prices ──────────────────────────────────────────────────────────────────

def load_prices(path):
    data = json.loads(Path(path).read_text())
    table = {}
    for m in data["models"]:
        inp = float(m["input"])
        table[m["match"]] = {
            "input": inp,
            "output": float(m["output"]),
            "write_5m": float(m.get("cache_write_5m", inp * 1.25)),
            "write_1h": float(m.get("cache_write_1h", inp * 2)),
            "read": float(m.get("cache_read", inp * 0.1)),
            "fast": float(m.get("fast", 1)),
        }
    return data, table


def rates_for(model, table):
    """Exact id, or a dated snapshot of it. Anything else stays unpriced."""
    if model in table:
        return table[model]
    base = DATED_SNAPSHOT.sub("", model)
    return table.get(base)


def split_cost(u, rates):
    k = rates["fast"] if u["fast"] else 1.0
    return {
        "input": k * u["inp"] * rates["input"] / 1e6,
        "write_5m": k * u["w5"] * rates["write_5m"] / 1e6,
        "write_1h": k * u["w1"] * rates["write_1h"] / 1e6,
        "read": k * u["read"] * rates["read"] / 1e6,
        "output": k * u["out"] * rates["output"] / 1e6,
    }


# ── Transcripts ─────────────────────────────────────────────────────────────

def parse_ts(value):
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def usage_of(message):
    u = message.get("usage") or {}
    split = u.get("cache_creation") or {}
    w5 = split.get("ephemeral_5m_input_tokens") or 0
    w1 = split.get("ephemeral_1h_input_tokens") or 0
    if not (w5 or w1):                                  # older transcripts do not split TTLs
        w5 = u.get("cache_creation_input_tokens") or 0
    return {
        "inp": u.get("input_tokens") or 0, "w5": w5, "w1": w1,
        "read": u.get("cache_read_input_tokens") or 0,
        "out": u.get("output_tokens") or 0,
        "think": (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
        "fast": u.get("speed") == "fast",
    }


def is_prompt(entry):
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    else:
        return False
    text = text.strip()
    return bool(text) and not text.startswith(NOT_PROMPTS)


def read_session(path):
    """One transcript -> its API responses (deduplicated) and prompt times."""
    responses = {}
    prompts = []
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line and '"user"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = entry.get("type")
            if kind == "user":
                if is_prompt(entry):
                    prompts.append(parse_ts(entry.get("timestamp")))
                continue
            if kind != "assistant":
                continue
            message = entry.get("message") or {}
            model = message.get("model") or ""
            if not model or model.startswith("<"):      # "<synthetic>" limit notices
                continue
            key = message.get("id") or entry.get("requestId")
            if not key:
                continue
            # Claude Code writes one line per content block, all carrying the
            # response's usage; keep the most complete copy.
            usage = usage_of(message)
            seen = responses.get(key)
            if seen is None or usage["out"] > seen["usage"]["out"]:
                responses[key] = {"ts": parse_ts(entry.get("timestamp")), "model": model,
                                  "usage": usage}
    ordered = sorted(responses.values(), key=lambda r: r["ts"] or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc))
    return ordered, prompts


def find_transcripts(root):
    for path in sorted(Path(root).glob("*/*.jsonl")):
        yield path, path.stem, False
    for path in sorted(Path(root).glob("*/*/subagents/*.jsonl")):
        yield path, path.parent.parent.name, True


# ── Analysis ────────────────────────────────────────────────────────────────

def analyse(root, table, since=None):
    components = collections.Counter()
    tokens = collections.Counter()
    by_model = collections.defaultdict(lambda: {"cost": 0.0, "responses": 0})
    unpriced = collections.Counter()
    whatif_tokens = []                                    # (usage) for re-pricing
    sessions = collections.defaultdict(lambda: {
        "cost": 0.0, "prompts": 0, "requests": 0, "context": 0, "first_context": None,
        "first_ts": None, "last_ts": None, "has_main": False, "subagents": 0})
    main_ctx, sub_ctx = [], []
    sub_cost = 0.0
    cold = {"count": 0, "cost": 0.0, "idle_hours": 0.0}
    period = [None, None]

    for path, session_id, is_sub in find_transcripts(root):
        responses, prompt_times = read_session(path)
        if since:
            responses = [r for r in responses if r["ts"] and r["ts"].date() >= since]
            prompt_times = [t for t in prompt_times if t and t.date() >= since]
        prompts = len(prompt_times)
        if not responses:
            continue
        s = sessions[session_id]
        if is_sub:
            s["subagents"] += 1
        else:
            s["has_main"] = True
            s["prompts"] += prompts
        w1_total = sum(r["usage"]["w1"] for r in responses)
        w5_total = sum(r["usage"]["w5"] for r in responses)
        ttl = 3600 if w1_total >= w5_total and w1_total else 300
        previous = None
        for r in responses:
            u = r["usage"]
            ctx = u["inp"] + u["w5"] + u["w1"] + u["read"]
            rates = rates_for(r["model"], table)
            for key in ("inp", "w5", "w1", "read", "out", "think"):
                tokens[key] += u[key]
            whatif_tokens.append(u)
            (sub_ctx if is_sub else main_ctx).append(ctx)
            if rates is None:
                unpriced[r["model"]] += 1
                cost = 0.0
            else:
                parts = split_cost(u, rates)
                components.update(parts)
                cost = sum(parts.values())
                by_model[r["model"]]["cost"] += cost
            by_model[r["model"]]["responses"] += 1
            tokens["fast"] += u["fast"]
            s["cost"] += cost
            if is_sub:
                sub_cost += cost
            else:
                s["requests"] += 1
                s["context"] += ctx
                if s["first_context"] is None:
                    s["first_context"] = ctx
            if r["ts"]:
                period[0] = r["ts"] if period[0] is None else min(period[0], r["ts"])
                period[1] = r["ts"] if period[1] is None else max(period[1], r["ts"])
                if not is_sub:
                    s["first_ts"] = s["first_ts"] or r["ts"]
                    s["last_ts"] = r["ts"]
                # A gap longer than the cache lifetime means this request wrote
                # the whole prefix again instead of reading it.
                if previous is not None and previous["ts"]:
                    gap = (r["ts"] - previous["ts"]).total_seconds()
                    if gap > ttl:
                        cold["count"] += 1
                        cold["idle_hours"] += gap / 3600
                        if rates is not None:
                            cold["cost"] += (u["w5"] * rates["write_5m"] + u["w1"] * rates["write_1h"]) / 1e6
            previous = r

    total = sum(components.values())
    mains = [s for s in sessions.values() if s["has_main"]]
    if not mains and not total:
        return None

    buckets = []
    for low, high in BUCKETS:
        group = [s for s in mains if s["prompts"] >= low and (high is None or s["prompts"] <= high)]
        requests = sum(s["requests"] for s in group)
        prompts = sum(s["prompts"] for s in group)
        buckets.append({
            "prompts": f"{low}+" if high is None else f"{low}-{high}",
            "sessions": len(group),
            "context_per_request": round(sum(s["context"] for s in group) / requests) if requests else None,
            "cost_per_prompt": round(sum(s["cost"] for s in group) / prompts, 2) if prompts else None,
        })

    ranked = sorted(sessions.values(), key=lambda s: s["cost"], reverse=True)
    firsts = [s for s in mains if s["first_context"] is not None and s["first_ts"]]
    recent = sorted(firsts, key=lambda s: s["first_ts"])[-10:]
    inputs = tokens["inp"] + tokens["w5"] + tokens["w1"] + tokens["read"]

    whatif = {}
    for model, rates in table.items():
        whatif[model] = round(sum(sum(split_cost(u, rates).values()) for u in whatif_tokens), 2)

    return {
        "period": [p.isoformat() if p else None for p in period],
        "sessions": {"main": len(mains), "subagent_transcripts": sum(s["subagents"] for s in sessions.values()),
                     "with_subagents": sum(1 for s in mains if s["subagents"])},
        "total_cost": round(total, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
        "tokens": dict(tokens),
        "cache_hit_ratio": round(tokens["read"] / inputs, 4) if inputs else None,
        "thinking_share_of_output": round(tokens["think"] / tokens["out"], 4) if tokens["out"] else None,
        "by_model": {m: {"cost": round(v["cost"], 2), "responses": v["responses"]}
                     for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])},
        "buckets": buckets,
        "top10_share": round(sum(s["cost"] for s in ranked[:10]) / total, 4) if total else None,
        "long_sessions_share": round(sum(s["cost"] for s in mains if s["prompts"] > 30) / total, 4)
        if total else None,
        "median_prompts": statistics.median([s["prompts"] for s in mains]) if mains else None,
        "prefix_median": round(statistics.median(s["first_context"] for s in firsts)) if firsts else None,
        "prefix_recent_median": round(statistics.median(s["first_context"] for s in recent)) if recent else None,
        "median_context": {
            "main": round(statistics.median(main_ctx)) if main_ctx else None,
            "subagent": round(statistics.median(sub_ctx)) if sub_ctx else None,
        },
        "subagent_share": round(sub_cost / total, 4) if total else None,
        "cold_restarts": {"count": cold["count"], "cost": round(cold["cost"], 2),
                          "idle_hours": round(cold["idle_hours"], 1)},
        "whatif": dict(sorted(whatif.items(), key=lambda kv: kv[1])),
        "unpriced": dict(unpriced),
    }


# ── Output ──────────────────────────────────────────────────────────────────

def bar(share, width=28):
    full = share * width
    blocks = int(full)
    return "█" * blocks + ("▌" if full - blocks >= 0.5 else "")


def pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def money(x):
    return f"${x:,.2f}"


def ktok(n):
    return f"{n / 1000:.0f}k" if n is not None else "-"


def print_report(r, prices_meta):
    total = r["total_cost"] or 1e-9
    start, end = (p[:10] if p else "?" for p in r["period"])
    print("Claude Code usage report -- local transcripts, nothing leaves this machine")
    print(f"{r['sessions']['main']} sessions ({r['sessions']['subagent_transcripts']} subagent runs), "
          f"{start} to {end}. Priced at list rates as of "
          f"{prices_meta['as_of']}: {money(r['total_cost'])} API-equivalent.")
    print("On a subscription you spend plan usage, not dollars: trust the shares more than the totals.")

    print("\nWhere the spend goes")
    names = [("read", "cache reads"), ("write_1h", "cache writes, 1 h"), ("output", "output"),
             ("write_5m", "cache writes, 5 min"), ("input", "fresh input")]
    for key, label in sorted(names, key=lambda kv: -r["components"].get(kv[0], 0)):
        share = r["components"].get(key, 0) / total
        print(f"  {label:<20} {pct(share):>6}  {bar(share)}")

    print("\nBy model")
    for model, v in r["by_model"].items():
        note = "unpriced" if model in r["unpriced"] else pct(v["cost"] / total)
        print(f"  {model:<28} {note:>8}  {v['responses']:>7,} responses")

    print("\nContext re-sent per request, by session length")
    for b in r["buckets"]:
        if not b["sessions"]:
            continue
        cpp = money(b["cost_per_prompt"]) + "/prompt" if b["cost_per_prompt"] is not None else ""
        print(f"  {b['prompts']:>6} prompts  {b['sessions']:>4} sessions  "
              f"{ktok(b['context_per_request']):>6} tokens  {cpp}")

    print("\nConcentration")
    print(f"  top 10 sessions: {pct(r['top10_share'])} of spend; sessions over 30 prompts: "
          f"{pct(r['long_sessions_share'])}; median session: {r['median_prompts'] or 0:g} prompts")

    print("\nPrompt cache")
    print(f"  {pct(r['cache_hit_ratio'])} of all input was read from cache")
    c = r["cold_restarts"]
    print(f"  {c['count']} cold restarts after idle gaps longer than the cache lifetime "
          f"({c['idle_hours']:g} idle hours), costing {money(c['cost'])} in rewrites")

    print("\nFixed prefix (system prompt, tools, instruction and memory files)")
    print(f"  first request of a session: median {ktok(r['prefix_median'])} tokens; "
          f"last 10 sessions: {ktok(r['prefix_recent_median'])}")
    print(f"  a typical request re-sends {ktok(r['median_context']['main'])} tokens in total")

    print("\nSubagents")
    print(f"  {pct(r['subagent_share'])} of spend; median {ktok(r['median_context']['subagent'])} "
          f"tokens per request against {ktok(r['median_context']['main'])} in main sessions")
    if r["thinking_share_of_output"] is not None:
        print(f"\nThinking: {pct(r['thinking_share_of_output'])} of all output tokens")

    print("\nThe same history priced on one model")
    groups = collections.OrderedDict()
    for model, cost in r["whatif"].items():                  # models sharing a price tier
        groups.setdefault(cost, []).append(model)
    for cost, models in groups.items():
        name = models[0] + (f" (+{len(models) - 1} at the same price)" if len(models) > 1 else "")
        delta = (cost - r["total_cost"]) / total
        print(f"  {name:<40} {money(cost):>12}  {delta * 100:+.0f}%")

    if r["unpriced"]:
        missing = ", ".join(f"{m} ({n})" for m, n in r["unpriced"].items())
        print(f"\nNot priced (add them to prices.json): {missing}", file=sys.stderr)


def main():
    default_root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--projects", default=str(default_root), help="transcript directory")
    ap.add_argument("--prices", default=str(HERE / "prices.json"), help="price table")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="only responses from this date")
    ap.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = ap.parse_args()

    meta, table = load_prices(args.prices)
    report = analyse(args.projects, table, args.since)
    if report is None:
        sys.exit(f"no Claude Code transcripts found under {args.projects}")
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print_report(report, meta)


if __name__ == "__main__":
    main()
