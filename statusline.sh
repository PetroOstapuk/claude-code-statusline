#!/usr/bin/env bash
# claude-code-statusline -- a two-line status line for Claude Code.
# https://github.com/PetroOstapuk/claude-code-statusline
#
#   line 1: repo, branch with its issue key as a link, PR review state,
#           context bar, model, mode flags
#   line 2: cost and burn rate, lines changed, session clock, prompt-cache
#           timer, every rate-limit window with its reset time
#
# Claude Code pipes a JSON payload to stdin; whatever this prints becomes the
# status line. It runs on every update, so it stays cheap: at most three
# external processes per render (jq, git, and date, which bash 5 skips).
# Everything else is bash builtins.
#
# Payload data never reaches a printf format string or echo -e, and control
# bytes are stripped from every string that gets printed, so neither a crafted
# directory name nor a crafted branch can inject escape sequences.
#
# Settings: ~/.config/claude-statusline/config (see README.md). The file is
# parsed line by line, never sourced.
#
# Debug: touch ~/.claude/.statusline-debug and the next render dumps the raw
# payload to ~/.claude/.statusline-last.json. Delete both afterwards: the dump
# holds your session data.

input=$(cat)

CLAUDE_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
[ -f "$CLAUDE_DIR/.statusline-debug" ] && printf '%s' "$input" > "$CLAUDE_DIR/.statusline-last.json"

is_num() { case "$1" in ""|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# ── Settings: defaults first, the config file overrides them ────────────────
ISSUE_URL=""               # link template, {key} is the issue key; "" = no links
STRIP_BRANCH_PREFIX=0      # 1 = hide whatever precedes the issue key
CTX_BAR_WIDTH=20
LIMIT_BAR_WIDTH=6          # mini-bars for rate limits; dropped when >2 windows
COMPACT_BELOW=120          # terminal narrower than this -> compact layout; 0 = never

# Icons. Each is either a plain symbol (1 column, takes the segment's colour)
# or an emoji with default emoji presentation (always 2 columns). Text-default
# emoji such as ♨ or ⏱ are avoided on purpose: terminals disagree on their
# width, and the mismatch shifts everything after them.
ICON_BRANCH="🌱"
ICON_MODEL="🚀"
ICON_CLOCK="◷"
ICON_CACHE_WARM="☕"        # the cache stays warm this long: your break budget
ICON_CACHE_COLD="🧊"
ICON_FAST="⚡"
ICON_THINK_OFF="💤"

CONFIG_FILE=${CLAUDE_STATUSLINE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/claude-statusline/config}
if [ -r "$CONFIG_FILE" ]; then
  while IFS= read -r cfg || [ -n "$cfg" ]; do
    cfg=${cfg//[[:cntrl:]]/}                   # also drops a Windows \r
    case "$cfg" in *=*) ;; *) continue ;; esac
    cfg_key=${cfg%%=*}; cfg_key=${cfg_key//[[:space:]]/}
    cfg_val=${cfg#*=}
    cfg_val=${cfg_val#"${cfg_val%%[![:space:]]*}"}   # trim leading blanks
    cfg_val=${cfg_val%"${cfg_val##*[![:space:]]}"}   # trim trailing blanks
    case "$cfg_key" in
      ISSUE_URL) ISSUE_URL=$cfg_val ;;
      STRIP_BRANCH_PREFIX|CTX_BAR_WIDTH|LIMIT_BAR_WIDTH|COMPACT_BELOW)
        is_num "$cfg_val" && printf -v "$cfg_key" '%s' "$cfg_val" ;;
      ICON_BRANCH|ICON_MODEL|ICON_CLOCK|ICON_CACHE_WARM|ICON_CACHE_COLD|ICON_FAST|ICON_THINK_OFF)
        printf -v "$cfg_key" '%s' "$cfg_val" ;;
    esac
  done < "$CONFIG_FILE"
fi
(( CTX_BAR_WIDTH > 60 ))   && CTX_BAR_WIDTH=60
(( LIMIT_BAR_WIDTH > 20 )) && LIMIT_BAR_WIDTH=20

# The link goes into an OSC 8 escape sequence, so only a plain http(s) URL is
# accepted. Without a {key} placeholder the key is appended as the last path
# segment, which is what Jira's /browse/ expects.
KEY_PLACEHOLDER='{key}'
case "$ISSUE_URL" in http://*|https://*) ;; *) ISSUE_URL="" ;; esac
case "$ISSUE_URL" in *[[:space:]]*|*\"*|*\'*|*\\*) ISSUE_URL="" ;; esac
case "$ISSUE_URL" in ""|*"$KEY_PLACEHOLDER"*) ;; *) ISSUE_URL="${ISSUE_URL%/}/$KEY_PLACEHOLDER" ;; esac

# One clock reading for everything time-based. bash 5 has it for free; the
# test hook pins it so the reset times in the tests are deterministic.
NOW=${CLAUDE_STATUSLINE_TEST_NOW:-${EPOCHSECONDS:-}}
is_num "$NOW" || NOW=$(date +%s)
is_num "$NOW" || NOW=0

# Claude Code sets COLUMNS to the terminal width before running the script.
COMPACT=0
if is_num "${COLUMNS:-}" && (( COMPACT_BELOW > 0 && COLUMNS < COMPACT_BELOW )); then
  COMPACT=1
  (( CTX_BAR_WIDTH > 10 )) && CTX_BAR_WIDTH=10
  LIMIT_BAR_WIDTH=0
fi

# ── Colours (literal escapes, never built from payload data) ────────────────
ESC=$'\033'; BEL=$'\a'
RESET="${ESC}[0m"; BOLD="${ESC}[1m"; DIM="${ESC}[2m"; UNDERLINE="${ESC}[4m"
CYAN="${ESC}[36m"; GREEN="${ESC}[32m"; YELLOW="${ESC}[33m"
RED="${ESC}[31m"; MAGENTA="${ESC}[35m"
LINK_BLUE="${ESC}[38;2;87;157;255m"
EMPTY_COLOR="${ESC}[38;2;60;60;60m"
EMPTY_BLOCK="${EMPTY_COLOR}░"
BRANCH_STYLE="${BOLD}${CYAN}"

# ── One jq pass for everything ──────────────────────────────────────────────
# Numbers are rounded and formatted inside jq, so bash never needs printf %f,
# which would break in locales that use a decimal comma.
#
# Rate limits are read generically from whatever windows the payload carries,
# so new ones show up without a code change.
#
# Booleans go through explicit if/then rather than `// ""`: in jq the
# alternative operator fires on `false` as well as `null`, which would turn
# every "feature is off" into "feature is unknown".
payload=$(printf '%s' "$input" | jq -r --argjson now "$NOW" '
  def num: if type == "number" then . else null end;
  def int: num | if . == null then null else (. + 0.5 | floor) end;
  def money: num | if . == null then null else
      ((. * 100 + 0.5) | floor) as $c
      | ($c % 100 | tostring) as $cents
      | "\(($c - ($c % 100)) / 100)." + (if ($cents | length) < 2 then "0" + $cents else $cents end)
    end;
  def str: tostring | split("\u001f") | join("");
  def slug: tostring | explode
    | map(select((. >= 97 and . <= 122) or (. >= 48 and . <= 57) or . == 95)) | implode;
  # Absolute reset clock: just the time for today, the weekday within six
  # days, the date beyond that.
  def clock($t): ($t | localtime) as $r
    | if ($r | strftime("%Y-%m-%d")) == ($now | localtime | strftime("%Y-%m-%d"))
        then $r | strftime("%H:%M")
      elif ($t - $now) < 518400 then $r | strftime("%a %H:%M")
      else $r | strftime("%d.%m %H:%M") end;

  [ (.model.display_name // "" | str),
    (.context_window.used_percentage | int // ""),
    (.cost.total_cost_usd | money // "0.00"),
    (.cost.total_lines_added | int // 0),
    (.cost.total_lines_removed | int // 0),
    (.cost.total_duration_ms | int // 0),
    (.workspace.current_dir // .cwd // "" | str),

    # Burn rate in $/h, suppressed for the first minute, where a few cents
    # over a few seconds produce a nonsense headline number.
    ( ((.cost.total_duration_ms | num) // 0) as $ms
      | ((.cost.total_cost_usd | num) // 0) as $usd
      | if $ms >= 60000 and $usd > 0 then ($usd * 3600000 / $ms | money) else "" end ),

    (.pr.number | int // ""),
    (.pr.review_state // "" | str),

    (.prompt_cache.expires_at | int // ""),
    ( if .prompt_cache.warm == true then "1"
      elif .prompt_cache.warm == false then "0" else "" end ),
    ((.prompt_cache.hit_ratio | num) as $h | if $h == null then "" else ($h * 100 + 0.5 | floor) end),

    (.effort.level // "" | str),
    ( if .fast_mode == true then "1" else "" end ),
    ( if .thinking.enabled == false then "1" else "" end ),

    ( [ (.rate_limits // {}) | objects | to_entries[]
        | .key as $k | (.value | objects) as $v
        | ($v.used_percentage | int) as $p | select($p != null)
        | ($v.resets_at | num) as $t
        | "\($k | slug)|\($p)|\(if $t == null then "" else ($t | floor) end)|\(if $t == null then "" else clock($t) end)"
      ] | join(";") )
  ] | @tsv
' 2>/dev/null)
# @tsv guarantees no raw tab or newline survives inside a value, so swapping
# tabs for \x1f is exact -- and \x1f, unlike tab, is not IFS whitespace, so
# empty fields keep their slot instead of collapsing and shifting everything
# left. (str already removed any \x1f from the values themselves.)
# NB: this must happen here, not inside the heredoc -- a heredoc body expands
# like a double-quoted string, where $'\t' is a literal, not an ANSI-C escape.
payload=${payload//$'\t'/$'\x1f'}
IFS=$'\x1f' read -r model used cost lines_add lines_del dur_ms cwd burn \
  pr_num pr_state cache_exp cache_warm cache_ratio \
  effort fast think_off limits <<EOF
$payload
EOF

# If jq failed (malformed payload, jq missing) every field is empty -- fall
# back to something printable rather than emitting errors.
[ -n "$model" ]     || model="?"
[ -n "$cost" ]      || cost="0.00"
is_num "$lines_add" || lines_add=0
is_num "$lines_del" || lines_del=0
is_num "$dur_ms"    || dur_ms=0
model=${model//[[:cntrl:]]/}
effort=${effort//[[:cntrl:]]/}
pr_state=${pr_state//[[:cntrl:]]/}
cwd=${cwd//\\\\/\\}                            # undo @tsv's backslash doubling
(( COMPACT )) && model=${model%% (*}           # "Opus 5.5 (1M context)" -> "Opus 5.5"

# ── Git: toplevel and branch in a single call ───────────────────────────────
repo=""; branch=""
if [ -n "$cwd" ]; then
  git_out=$(git -C "$cwd" --no-optional-locks rev-parse --show-toplevel --abbrev-ref HEAD 2>/dev/null)
  if [ -n "$git_out" ]; then
    repo=${git_out%%$'\n'*}; repo=${repo##*/}          # builtin basename, no fork
    case "$git_out" in *$'\n'*) branch=${git_out#*$'\n'} ;; esac
    # A detached HEAD reads as the literal "HEAD": show the short sha instead.
    # A repository without commits reads the same way but has no sha yet, so
    # fall back to the name of the branch it is on.
    if [ "$branch" = "HEAD" ]; then
      branch=$(git -C "$cwd" --no-optional-locks rev-parse --verify -q --short HEAD 2>/dev/null) ||
        branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short -q HEAD 2>/dev/null)
    fi
  fi
fi
# Filesystem-controlled strings are the one place raw control bytes can reach
# the terminal: a directory literally named $'ev\033[2Jil' would otherwise
# clear the screen on every render. printf '%s' does not help -- these are
# real 0x1b bytes, not backslash escapes -- so they are dropped outright.
repo=${repo//[[:cntrl:]]/}
branch=${branch//[[:cntrl:]]/}

# Keep the whole branch -- the tail is what says what the work is -- but give
# the issue key its own look: link blue, underlined and clickable (OSC 8) when
# ISSUE_URL is set. At least three capitals on purpose: two would also turn
# the "RC-2" of a release/141-RC-2 branch into a key.
branch_render=$branch
key_re='[[:upper:]]{3,}-[[:digit:]]+'
handle_re='^[[:lower:]]{2,5}_.'
if [ -n "$branch" ] && [[ $branch =~ $key_re ]]; then
  key=${BASH_REMATCH[0]}
  head=${branch%%"$key"*}
  tail=${branch#*"$key"}
  [ "$STRIP_BRANCH_PREFIX" = "1" ] && head=""
  key_style="${BOLD}${LINK_BLUE}"
  key_text=$key
  if [ -n "$ISSUE_URL" ]; then
    key_style="${key_style}${UNDERLINE}"
    # OSC 8 hyperlink, BEL-terminated (wider terminal support than ST).
    key_text="${ESC}]8;;${ISSUE_URL//"$KEY_PLACEHOLDER"/$key}${BEL}${key}${ESC}]8;;${BEL}"
  fi
  branch_render="${head}${RESET}${key_style}${key_text}${RESET}${BRANCH_STYLE}${tail}"
elif [ "$STRIP_BRANCH_PREFIX" = "1" ] && [[ $branch =~ $handle_re ]]; then
  # No key, but a short personal handle in front ("abc_fix-typo"). Bounded to
  # 2-5 letters so that words such as "feature_" or "hotfix_" stay.
  branch_render=${branch#*_}
fi

# ── Helpers ─────────────────────────────────────────────────────────────────

# Green -> yellow -> red gradient bar. Sets $BAR. No subshells: printf -v is
# a builtin, so a 20-block bar costs zero forks.
build_bar() {
  local pct=$1 width=$2 filled i pos r g b chunk
  filled=$(( (pct * width + 50) / 100 ))
  (( filled > width )) && filled=$width
  BAR=""
  for (( i = 0; i < width; i++ )); do
    if (( i < filled )); then
      pos=$(( i * 100 / (width > 1 ? width - 1 : 1) ))
      if (( pos <= 50 )); then
        r=$(( 220 * pos / 50 )); g=200; b=$(( 80 - 80 * pos / 50 ))
      else
        r=220; g=$(( 200 - 160 * (pos - 50) / 50 )); b=$(( 20 * (pos - 50) / 50 ))
      fi
      printf -v chunk '%s[38;2;%d;%d;%dm█' "$ESC" "$r" "$g" "$b"
    else
      chunk=$EMPTY_BLOCK
    fi
    BAR="${BAR}${chunk}"
  done
  BAR="${BAR}${RESET}"
}

pct_color() {  # sets $PCOL -- higher is worse
  if   (( $1 >= 90 )); then PCOL=$RED
  elif (( $1 >= 70 )); then PCOL=$YELLOW
  else                      PCOL=$GREEN; fi
}

pct_emoji() {  # sets $PEMO -- a traffic light, one step ahead of the % colour
  if   (( $1 >= 90 )); then PEMO="🔴"
  elif (( $1 >= 70 )); then PEMO="🟠"
  elif (( $1 >= 50 )); then PEMO="🟡"
  else                      PEMO="🟢"; fi
}

fmt_eta() {  # epoch seconds -> compact "in" duration; sets $ETA
  local d=$(( $1 - NOW ))
  if   (( d <= 0 ));    then ETA="now"
  elif (( d < 3600 ));  then ETA="$(( d / 60 ))m"
  elif (( d < 86400 )); then ETA="$(( d / 3600 ))h$(( (d % 3600) / 60 ))m"
  else                       ETA="$(( d / 86400 ))d$(( (d % 86400) / 3600 ))h"; fi
}

limit_label() {  # sets $LBL
  case "$1" in
    five_hour)            LBL="5h" ;;
    seven_day)            LBL="7d" ;;
    seven_day_opus)       LBL="7d·opus" ;;
    seven_day_sonnet)     LBL="7d·sonnet" ;;
    seven_day_oauth_apps) LBL="7d·apps" ;;
    spend_limit)          LBL="spend" ;;
    *)                    LBL=$1 ;;
  esac
}

sep="${DIM}│${RESET}"

# ── Line 1: repo · branch · PR · context · model · mode flags ───────────────
if is_num "$used"; then
  build_bar "$used" "$CTX_BAR_WIDTH"; pct_color "$used"; pct_emoji "$used"
  ctx_part="${PEMO} ${BAR} ${PCOL}${used}%${RESET}"
else
  # no reading yet: a neutral light, not a green one that claims "all fine"
  printf -v blanks '%*s' "$CTX_BAR_WIDTH" ""
  ctx_part="⚪ ${EMPTY_COLOR}${blanks// /░}${RESET} ${DIM}--%${RESET}"
fi

pr_part=""
if [ -n "$pr_num" ]; then
  # The footer already shows the PR number; what it lacks is the review state.
  case "$pr_state" in
    approved)          pr_part="${GREEN}✓${RESET}" ;;
    changes_requested) pr_part="${RED}✗${RESET}" ;;
    draft)             pr_part="${DIM}◌${RESET}" ;;
    *)                 pr_part="${YELLOW}·${RESET}" ;;
  esac
fi

# Only states that deviate from the default earn a column: thinking is on
# unless you turned it off, so its marker means "off", not "on".
flags=""
[ -n "$effort" ]       && flags="${flags} ${DIM}·${effort}${RESET}"
[ "$fast" = "1" ]      && flags="${flags} ${ICON_FAST}"
[ "$think_off" = "1" ] && flags="${flags} ${ICON_THINK_OFF}"

line1=""
[ -n "$repo" ]          && line1="${BOLD}${YELLOW}${repo}${RESET}"
[ -n "$branch_render" ] && line1="${line1:+$line1 }${BRANCH_STYLE}${ICON_BRANCH} ${branch_render}${RESET}"
# the review state describes the branch, so it never stands on its own
[ -n "$pr_part" ] && [ -n "$branch_render" ] && line1="${line1} ${pr_part}"
line1="${line1:+$line1 $sep }${ctx_part}"
line1="${line1} ${sep} ${MAGENTA}${ICON_MODEL} ${model}${RESET}${flags}"

# ── Line 2: cost · velocity · clock · prompt cache · rate limits ────────────
line2="${YELLOW}\$${cost}${RESET}"
if [ -n "$burn" ] && (( ! COMPACT )); then
  line2="${line2} ${DIM}(\$${burn}/h)${RESET}"
fi
line2="${line2} ${sep} ${GREEN}+${lines_add}${RESET} ${RED}-${lines_del}${RESET}"

if (( ! COMPACT )); then
  secs=$(( dur_ms / 1000 ))
  if (( secs >= 3600 )); then printf -v clock '%dh %dm' $(( secs / 3600 )) $(( (secs % 3600) / 60 ))
  else                        printf -v clock '%dm %ds' $(( secs / 60 )) $(( secs % 60 )); fi
  line2="${line2} ${sep} ${DIM}${ICON_CLOCK} ${clock}${RESET}"
fi

# Prompt cache: a cold cache means the whole context is re-billed at the write
# price on the next turn, so the time left is the actionable number here.
cache_part=""
if is_num "$cache_exp" && (( cache_exp > NOW )); then
  left=$(( cache_exp - NOW ))
  fmt_eta "$cache_exp"
  if   (( left < 300 )); then ccol=$RED
  elif (( left < 900 )); then ccol=$YELLOW
  else                        ccol=$GREEN; fi
  cache_part="${ccol}${ICON_CACHE_WARM} ${ETA}${RESET}"
fi
[ -z "$cache_part" ] && [ "$cache_warm" = "0" ] && cache_part="${DIM}${ICON_CACHE_COLD} cold${RESET}"
if [ -n "$cache_part" ] && is_num "$cache_ratio" && (( ! COMPACT )); then
  cache_part="${cache_part} ${DIM}${cache_ratio}%${RESET}"
fi
[ -n "$cache_part" ] && line2="${line2} ${sep} ${cache_part}"

if [ -n "$limits" ]; then
  # count the windows first: bars only stay readable while there are one or two
  saved_ifs=$IFS; set -f; IFS=';'
  # shellcheck disable=SC2086  # splitting on ';' is the point
  set -- $limits
  IFS=$saved_ifs; set +f
  bar_w=$LIMIT_BAR_WIDTH
  (( $# > 2 )) && bar_w=0

  for entry in "$@"; do
    lkey=${entry%%|*}; rest=${entry#*|}
    lpct=${rest%%|*};  rest=${rest#*|}
    lreset=${rest%%|*}; ldate=${rest#*|}
    is_num "$lpct" || continue
    limit_label "$lkey"; pct_color "$lpct"
    piece="${DIM}${LBL}${RESET} "
    if (( bar_w > 0 )); then build_bar "$lpct" "$bar_w"; piece="${piece}${BAR} "; fi
    piece="${piece}${PCOL}${lpct}%${RESET}"
    if is_num "$lreset"; then fmt_eta "$lreset"; piece="${piece} ${DIM}↻${ETA}${RESET}"; fi
    [ -n "$ldate" ] && piece="${piece} ${DIM}(${ldate})${RESET}"
    line2="${line2} ${sep} ${piece}"
  done
fi

printf '%s\n%s\n' "$line1" "$line2"
