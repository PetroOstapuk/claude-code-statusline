# Where Claude Code's tokens go, and how to spend fewer

This is a practical guide to what makes a Claude Code session expensive. It is
built on one real sample: about five weeks of daily work by one developer,
76 sessions plus 201 subagent runs, re-priced at list prices. The sample is
anonymous and only its proportions are shown, because proportions are what
carry over to your own work. To get your own numbers, run
[`tools/usage-report.py`](../tools/usage-report.py) (see
[Measure your own usage](#measure-your-own-usage)).

Prices and product behaviour are as of September 2026. They change, so each
claim links to its source where it has one.

## The short version

1. **You pay for re-reading, not for asking.** Every turn re-sends the whole
   conversation. Cache reads were 55% of the spend; fresh input, meaning what
   you type, was about 0%.
2. **The model generation matters more than the model family.** Newer models
   read cached context at a quarter to half the price of the previous ones,
   and cache reads dominate the bill.
3. **Long sessions cost more per prompt.** The context re-sent with each
   request grew from about 140k to about 370k tokens as sessions got longer.
   The ten biggest sessions took 58% of the spend.
4. **A cold cache is re-bought at the write price.** After an idle gap the
   next request rewrites the entire context. That was 9% of the spend in the
   sample, and on the newest models a cold turn costs 40 to 80 times a warm
   one.
5. **Every session starts at roughly 64k tokens** before you type a word:
   system prompt, tool definitions, instruction and memory files.
6. **Keep doing what already works.** The cache hit rate was 98%, subagents
   kept their own context small, and short, precise prompts were never the
   problem.

## 1. You pay for re-reading, not for asking

The model keeps nothing between requests, so Claude Code sends the full
context every time: system prompt, project instructions, every earlier message
and tool result, and your new message
([how Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)).
Prompt caching makes the unchanged part cheap to re-send, but not free.

Where the sample's spend went:

| Component | Share | |
|---|---:|---|
| Cache reads (re-sent context) | 55.0% | `███████████████▌` |
| Cache writes, 1-hour lifetime | 19.0% | `█████▌` |
| Output (answers and thinking) | 16.8% | `█████` |
| Cache writes, 5-minute lifetime | 9.1% | `██▌` |
| Fresh input (what you type) | 0.0% | |

Your prompts cost almost nothing. What costs is how much context each prompt
makes the model read again, and how often.

## 2. How one request is billed

Each request is billed in token categories. Relative to the price of fresh
input tokens ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Token kind | Price | Notes |
|---|---|---|
| Fresh input | 1× | the part after the cached prefix |
| Cache write, 5-minute lifetime | 1.25× | |
| Cache write, 1-hour lifetime | 2× | |
| Cache read | 0.1× | Opus 5.5: 0.05×, Fable 5.1: 0.025× |
| Output | 5× | includes thinking tokens |

Which cache lifetime you get depends on how you pay. On a Claude subscription,
within the plan's included usage, the main conversation uses the 1-hour cache.
With an API key, usage credits or a cloud provider it uses 5 minutes, and so do
subagents everywhere. You can choose with the `promptCacheTtl` setting or
`CLAUDE_CODE_PROMPT_CACHE_TTL` ([cache lifetime](https://code.claude.com/docs/en/prompt-caching#cache-lifetime)).

## 3. Lever one: the model, and its generation

Since cache reads dominate, the cache-read price is the rate that matters most:

| Model | Input | Output | Cache read |
|---|---:|---:|---:|
| Fable 5 | $10 | $50 | $1.00 |
| Fable 5.1 | $10 | $50 | **$0.25** |
| Opus 5 | $5 | $25 | $0.50 |
| Opus 5.5 | $4 | $20 | **$0.20** |
| Sonnet 5 | $2 | $10 | $0.20 |
| Haiku 4.5 | $1 | $5 | $0.10 |

*USD per million tokens, list prices, September 2026.*

The sample's whole token history, priced as if every request had gone to one
model, compared with what was actually spent:

| All on | Compared with actual |
|---|---:|
| Fable 5 | +60% |
| Fable 5.1 | −14% |
| Opus 5 | −20% |
| Opus 5.5 | −56% |
| Sonnet 5 | −68% |
| Haiku 4.5 | −84% |

Priced on Fable 5.1, the sample's Fable 5 requests would have cost **42%
less**. Its Opus 5 requests would have cost **44% less** on Opus 5.5. Within
each family nothing changes but the cache-read price, and the newer model is
the stronger one. Fable 5 alone took 61% of the sample's spend, so that one
switch would have cut the total by about a quarter.

What to do:

- Use the newest generation of whichever family you pick. The older one is
  both weaker and more expensive to keep a long session going on.
- Make an efficient flagship (Opus 5.5 at the time of writing) your default,
  and switch to the most capable model on purpose, for work that needs it:
  root-cause analysis, architecture, long migrations.
- Pick the model when the session starts. Each model has its own cache, so a
  mid-session switch re-sends the whole conversation uncached
  ([switching models](https://code.claude.com/docs/en/prompt-caching#switching-models)).

## 4. Lever two: session length

Every request carries the whole conversation so far, so each prompt in a long
session costs more than the same prompt in a short one:

| Prompts in the session | Sessions | Context re-sent per request |
|---|---:|---:|
| 1–5 | 42 | 139k tokens |
| 6–15 | 15 | 191k tokens |
| 16–30 | 13 | 374k tokens |
| 31–60 | 6 | 369k tokens |

A prompt in a 16–30-prompt session cost about 2.4 times one in a short
session. (Cost per prompt is noisier than context size, because the model mix
differs between sessions.) Spend is concentrated: the median session had 5
prompts, yet the ten biggest sessions took 58% of the spend.

What to do:

- **New task, new session.** `/clear` when the subject changes, instead of
  carrying the previous task's context into the next one.
- **Compact at natural breaks**, between tasks rather than mid-task. With a
  warm cache `/compact` is cheap; after a long break it re-reads the whole
  history uncached
  ([compacting](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation)).
- **Rewind instead of compacting** when you want to abandon a path. `/rewind`
  goes back to a prefix that is already cached.
- The status line's context light turns 🟡 at 50%, 🟠 at 70% and 🔴 at 90%.
  Treat 🟠 as the moment to wrap up or compact.

## 5. Lever three: effort

Effort sets how deeply the model reasons. Claude Code defaults to `high`
(`medium` on Opus 5.5, `xhigh` on Opus 4.7). The documentation describes `max`
as a setting that "can improve performance on demanding tasks but may show
diminishing returns and is prone to overthinking. Test before adopting
broadly" ([effort levels](https://code.claude.com/docs/en/model-config)).

In the sample, thinking was 54% of all output tokens, which is about 9% of the
total spend. Effort controls that part directly, and the length of the turns
indirectly.

What to do:

- Don't pin `max` for everything, whether through `effortLevel`,
  `CLAUDE_CODE_EFFORT_LEVEL` or `--effort`. Keep the default and use
  `/effort max` for the task that needs it.
- On most models a mid-session effort change also re-sends the conversation
  uncached. On Opus 5.5 and Fable 5.1 (API key or subscription) the cache
  survives it ([changing effort](https://code.claude.com/docs/en/prompt-caching#changing-effort-level)).

## 6. The cache timer

While the cached prefix is warm, re-sending it is billed as a cache read. When
it expires, the next request writes the entire context again at the write
price. The difference depends on the model:

| Model | Warm turn (read) | Cold turn (1-hour write) | Cold versus warm |
|---|---:|---:|---:|
| Fable 5, Opus 5 | $1.00 / $0.50 | $20 / $10 | 20× |
| Opus 5.5 | $0.20 | $8 | 40× |
| Fable 5.1 | $0.25 | $20 | 80× |

*Per million tokens of context.*

The newer models made warm turns cheaper and left cold ones expensive, so the
penalty for letting the cache expire grew. In the sample, 83 requests came
after an idle gap longer than the cache lifetime, and rewriting the context on
those requests was 9% of the whole spend.

This is what the status line's `☕ 47m` shows: how long the cache stays warm.
It turns yellow under 15 minutes and red under 5, and shows `🧊 cold` once the
cache has expired.

- If `☕` is red and you are about to step away, send what you meant to send
  now, or accept that the first turn back will rewrite everything.
- If you often come back after more than five minutes with an API key, the
  1-hour lifetime (`promptCacheTtl: "1h"`) can pay off. It doubles the write
  price, so it loses on short bursts of work
  ([cache lifetime](https://code.claude.com/docs/en/prompt-caching#cache-lifetime)).
- Keep the model and effort level stable within a session. Switching either,
  turning fast mode on, or changing which MCP tools sit in the prefix
  invalidates the cache
  ([what invalidates it](https://code.claude.com/docs/en/prompt-caching#actions-that-invalidate-the-cache)).

## 7. The fixed prefix

The first request of every session in the sample already carried a median of
**64k tokens** (72k in the ten most recent sessions): the system prompt, tool
definitions, `CLAUDE.md` / `AGENTS.md`, rules and memory files. That prefix is
re-sent on every turn, about a quarter of a typical 257k-token request.

- Run `/context` in a fresh session to see what the prefix is made of.
- Keep instruction files lean. They load into every session, whole.
- MCP servers cost less than they used to. With tool search, which is on by
  default for supported models, their tools are deferred and load when used.
  Servers marked `alwaysLoad`, or setups where tool search is unavailable, put
  every tool definition in the prefix
  ([MCP and the cache](https://code.claude.com/docs/en/prompt-caching#connecting-or-disconnecting-an-mcp-server)).
  Disconnect servers you don't use, and duplicates in particular.
- Your prefix grows with every Claude Code release, plugin and skill you add.
  It is worth rechecking `/context` now and then.

## 8. Memory and instruction files

If you keep project knowledge in memory files, the index that loads into
every session is part of the prefix above. A few lessons from trimming one:

- **Make the index a router, not a report.** One short line per entry that
  says what it is and where the detail lives. The detail goes in its own file
  and loads when needed. Cutting a long index this way removed about 40% of
  its tokens with no loss of facts.
- **Archive what is finished.** Closed topics can move to a separate archive
  index that is read on demand.
- **Mind the script.** Text in non-Latin scripts usually takes more tokens per
  character than English, so the always-loaded parts benefit most from
  English. Keep verbatim the phrases a rule is meant to match, because
  translating a trigger breaks it.
- **One writer at a time.** Two sessions editing the same memory files can
  silently overwrite each other's changes. After a big memory edit, restart
  the other sessions. `CLAUDE.md` changes only apply after `/clear`,
  `/compact` or a restart anyway
  ([editing CLAUDE.md mid-session](https://code.claude.com/docs/en/prompt-caching#editing-claude-md-mid-session)).
- **You rarely need a vector database.** Retrieval pays off when the knowledge
  no longer fits in context. A few hundred kilobytes of notes, with a flat
  index pointing to files, is simpler, faster and more reliable.

## 9. What already works: keep doing it

- **A high cache hit rate.** 98% of input came from cache, which is about the
  ceiling. Chasing the last points is not worth it. Protecting it, by not
  switching models or effort mid-task, is.
- **Subagents.** They took 25% of the spend but worked in their own, smaller
  context: 121k tokens per request against 257k in the main sessions.
  Exploration that would otherwise bloat the main conversation stays out of
  it.
- **Searching instead of reading whole files.** Anything read into the
  conversation is re-sent on every later turn.
- **Detailed prompts.** Fresh input costs close to nothing. A precise prompt
  that gets the answer in one turn beats a short one that needs five
  follow-ups, each of which re-sends the whole context.

## 10. What to do, by effect

| Action | Effect in the sample | Cost to quality |
|---|---|---|
| Move to the newest generation of your model family | −42…44% on those requests | none, the newer model is stronger |
| Default to an efficient flagship, and the top model on purpose | up to −56% | only on the hardest tasks, so choose those deliberately |
| New task, new session; compact between tasks | long sessions re-send about 2.7× the context | none |
| Mind the cache timer before a break | cold rewrites were 9% of spend | none |
| Keep effort at its default, `max` per task | thinking is ~9% of spend | test on your own tasks |
| Trim the prefix (`/context`, unused MCP servers, lean instruction files) | every turn, every session | none |

## Measure your own usage

```sh
python3 tools/usage-report.py                 # everything Claude Code still keeps
python3 tools/usage-report.py --since 2026-09-01
python3 tools/usage-report.py --json          # machine-readable
```

The script reads the transcripts Claude Code stores under `~/.claude/projects`.
It uses only the token counters stored with each response, re-prices them from
[`tools/prices.json`](../tools/prices.json), and prints the same breakdowns as
this guide. No prompt or response text makes it into the output, and nothing
leaves your machine. Models missing from the price table are listed as
unpriced rather than guessed.

## Method and caveats

- **Source.** Token counters from local Claude Code transcripts, one entry per
  API response, deduplicated by message id, subagent runs included and
  attributed to their parent session.
- **Prices.** List prices as of September 2026. The dollar figures are an
  API-price equivalent, the same way Claude Code estimates
  `cost.total_cost_usd`. On a subscription you spend plan usage, not dollars.
  How usage limits weigh each token kind is not published, so treat the shares
  as the reliable part.
- **Cold restarts.** A gap between two requests of a session that was longer
  than its cache lifetime, with the cost of that request's cache writes.
- **Prefix.** The size of the first request of each session, which is the
  fixed prefix plus a short first prompt.
- **One sample.** One developer's mix of models and habits. Yours will differ,
  which is what the script is for.
- **Retention.** Claude Code prunes old transcripts (the `cleanupPeriodDays`
  setting), so only what is still on disk can be measured.
