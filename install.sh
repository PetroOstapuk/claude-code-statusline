#!/usr/bin/env bash
# Installs claude-code-statusline for the current user. Run ./install.sh --help
# for the options.
#
# Safe to re-run: it changes only what you ask for and backs up every file it
# replaces. Your settings live in ~/.config/claude-statusline/config, outside
# this repository, so updating with `git pull` never touches them.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
SRC="$REPO_DIR/statusline.sh"
CLAUDE_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
TARGET="$CLAUDE_DIR/statusline.sh"
SETTINGS="$CLAUDE_DIR/settings.json"
CONFIG_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/claude-statusline
CONFIG="$CONFIG_DIR/config"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
MARKER="claude-code-statusline"          # present in the header of statusline.sh

say()  { printf '%s\n' "$*"; }
warn() { printf 'install.sh: %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
tilde() { local t='~'; printf '%s' "${1/#"$HOME"/$t}"; }   # bash 3.2 would keep a \~

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --issue-url URL          make issue keys in branch names clickable. URL is your
                           Jira site (https://acme.atlassian.net), any issue link
                           from it, or a template with {key} for other trackers
                           (https://linear.app/acme/issue/{key})
  --no-issue-url           turn issue links off
  --strip-branch-prefix    show ABC-123-login for abc_ABC-123-login or
                           feature/ABC-123-login
  --keep-branch-prefix     show branch names in full (the default)
  --copy                   copy statusline.sh instead of linking to this checkout
  --uninstall              remove the status line; keeps your settings file
  -h, --help               show this help

Without --issue-url, a first install asks for the tracker when run in a
terminal. Settings are kept in ~/.config/claude-statusline/config.
EOF
}

# ── Arguments ───────────────────────────────────────────────────────────────
ACTION="install"
MODE="link"
ISSUE_SET=0
ISSUE_ARG=""
STRIP_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --issue-url)   [ $# -ge 2 ] || die "--issue-url needs a value"
                   ISSUE_SET=1; ISSUE_ARG=$2; shift 2 ;;
    --issue-url=*) ISSUE_SET=1; ISSUE_ARG=${1#*=}; shift ;;
    --no-issue-url) ISSUE_SET=1; ISSUE_ARG=""; shift ;;
    --strip-branch-prefix) STRIP_ARG=1; shift ;;
    --keep-branch-prefix)  STRIP_ARG=0; shift ;;
    --copy)        MODE=copy; shift ;;
    --uninstall)   ACTION=uninstall; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option: $1 (see --help)" ;;
  esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

need_jq() {
  command -v jq >/dev/null 2>&1 ||
    die "jq is required. macOS: brew install jq. Debian/Ubuntu: sudo apt-get install jq"
  local v
  v=$(jq --version 2>/dev/null || true); v=${v#jq-}
  case "$v" in
    1.[0-5]|1.[0-5].*|1.[0-5]-*) die "jq $v is too old: the status line needs 1.6 or newer" ;;
  esac
}

# Turns whatever the user pasted into a link template with {key}.
normalize_issue_url() {
  local u=${1//[[:space:]]/} host
  [ -n "$u" ] || return 0
  case "$u" in http://*|https://*) ;; *) u="https://$u" ;; esac
  case "$u" in
    *'{key}'*)            printf '%s\n' "$u" ;;
    */browse/*|*/browse)  printf '%s/browse/{key}\n' "${u%%/browse*}" ;;
    *)
      host=${u#*://}; host=${host%%/*}
      case "$host" in
        # Jira Cloud: any page of the site works, the issue path is fixed
        *.atlassian.net) printf '%s/browse/{key}\n' "${u%%://*}://$host" ;;
        # self-hosted Jira: keep the base path, it may carry a context root
        *)               printf '%s/browse/{key}\n' "${u%/}" ;;
      esac ;;
  esac
}

valid_template() {
  case "$1" in http://?*|https://?*) ;; *) return 1 ;; esac
  case "$1" in *\"*|*\'*|*\\*|*'<'*|*'>'*) return 1 ;; esac
  case "$1" in *'{key}'*) return 0 ;; *) return 1 ;; esac
}

set_config() {  # set_config KEY VALUE -- rewrites one key; an empty VALUE removes it
  local key=$1 val=$2 tmp
  mkdir -p "$CONFIG_DIR"
  [ -f "$CONFIG" ] ||
    printf '# claude-code-statusline settings -- every key is described in README.md\n' > "$CONFIG"
  tmp=$(mktemp "$CONFIG_DIR/.config.XXXXXX")
  grep -v "^[[:space:]]*${key}[[:space:]]*=" "$CONFIG" > "$tmp" || true
  [ -z "$val" ] || printf '%s=%s\n' "$key" "$val" >> "$tmp"
  cat "$tmp" > "$CONFIG"
  rm -f "$tmp"
}

config_value() {
  [ -f "$CONFIG" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONFIG" | tail -n 1
}

ask_issue_url() {
  local answer=""
  say ""
  say "Clickable issue keys (optional)"
  say "  Jira: paste your site, e.g. https://acme.atlassian.net, or any issue link from it."
  say "  Other trackers: a link template with {key}, e.g. https://linear.app/acme/issue/{key}"
  printf 'Issue tracker URL, or Enter to skip: '
  IFS= read -r answer || answer=""
  if [ -n "$answer" ]; then ISSUE_SET=1; ISSUE_ARG=$answer; fi
}

# Writes a file through a temporary copy. `cat >` rather than `mv` keeps the
# original's permissions, and keeps a symlinked settings.json a symlink.
replace_contents() {  # replace_contents FILE < new-contents
  local file=$1 tmp
  tmp=$(mktemp "$(dirname "$file")/.${file##*/}.XXXXXX")
  cat > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

install_script() {
  mkdir -p "$CLAUDE_DIR"
  if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "$SRC" ]; then
    if [ "$MODE" = link ]; then
      say "already linked: $(tilde "$TARGET") -> $(tilde "$SRC")"
      return 0
    fi
    rm "$TARGET"
  elif [ -L "$TARGET" ] || [ -e "$TARGET" ]; then
    if [ -f "$TARGET" ] && [ ! -L "$TARGET" ] && grep -q "$MARKER" "$TARGET" 2>/dev/null; then
      rm "$TARGET"                        # an earlier --copy install of this project
    else
      mv "$TARGET" "$TARGET.bak-$STAMP"
      say "backed up your previous status line -> $(tilde "$TARGET.bak-$STAMP")"
    fi
  fi
  if [ "$MODE" = copy ]; then
    cp "$SRC" "$TARGET"
    chmod 755 "$TARGET"
    say "copied $(tilde "$SRC") -> $(tilde "$TARGET")"
  else
    ln -s "$SRC" "$TARGET"
    say "linked $(tilde "$TARGET") -> $(tilde "$SRC")"
  fi
}

wire_settings() {
  mkdir -p "$CLAUDE_DIR"
  if [ -f "$SETTINGS" ]; then
    jq empty "$SETTINGS" >/dev/null 2>&1 ||
      die "$SETTINGS is not valid JSON. Fix it and run this again; nothing in it was changed."
    if jq -e --arg cmd "$TARGET" \
         '.statusLine.type == "command" and .statusLine.command == $cmd' "$SETTINGS" >/dev/null 2>&1; then
      say "settings.json already uses it"
      return 0
    fi
    cp -p "$SETTINGS" "$SETTINGS.bak-$STAMP"
    say "backed up settings.json -> $(tilde "$SETTINGS.bak-$STAMP")"
  else
    printf '{}\n' > "$SETTINGS"
    chmod 600 "$SETTINGS"
  fi
  # Keeps any other statusLine field you set; forces only type and command.
  jq --arg cmd "$TARGET" \
     '.statusLine = ({padding: 0, refreshInterval: 10} + (.statusLine // {}) + {type: "command", command: $cmd})' \
     "$SETTINGS" | replace_contents "$SETTINGS"
  say "settings.json: statusLine -> $(tilde "$TARGET")"
}

# ── Actions ─────────────────────────────────────────────────────────────────

do_install() {
  local template example
  need_jq
  command -v git >/dev/null 2>&1 ||
    warn "git not found: the repository and branch stay hidden until it is installed"
  [ -f "$SRC" ] || die "statusline.sh is missing next to install.sh"
  [ -x "$SRC" ] || chmod +x "$SRC"

  if [ "$ISSUE_SET" = 0 ] && [ ! -f "$CONFIG" ] && [ -t 0 ] && [ -t 1 ]; then
    ask_issue_url
  fi

  if [ "$ISSUE_SET" = 1 ] && [ -n "$ISSUE_ARG" ]; then
    template=$(normalize_issue_url "$ISSUE_ARG")
    valid_template "$template" ||
      die "cannot use \"$ISSUE_ARG\" as an issue link: expected an http(s) address without quotes or brackets"
    set_config ISSUE_URL "$template"
  elif [ "$ISSUE_SET" = 1 ]; then
    set_config ISSUE_URL ""
  fi
  if [ -n "$STRIP_ARG" ]; then set_config STRIP_BRANCH_PREFIX "$STRIP_ARG"; fi
  [ -f "$CONFIG" ] || set_config ISSUE_URL ""       # leave a file to edit later

  install_script
  wire_settings

  template=$(config_value ISSUE_URL)
  if [ -n "$template" ]; then example="ABC-123 -> ${template//'{key}'/ABC-123}"; else example="off"; fi
  say ""
  say "Done. Claude Code shows the new status line on its next update."
  say "  issue links  $example"
  say "  settings     $(tilde "$CONFIG")"
  if [ "$MODE" = link ]; then
    say "  update       git -C $(tilde "$REPO_DIR") pull"
  else
    say "  update       git pull, then run ./install.sh --copy again"
  fi
}

do_uninstall() {
  local latest f
  if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "$SRC" ]; then
    rm "$TARGET"; say "removed $(tilde "$TARGET")"
  elif [ -f "$TARGET" ] && grep -q "$MARKER" "$TARGET" 2>/dev/null; then
    rm "$TARGET"; say "removed $(tilde "$TARGET")"
  elif [ -e "$TARGET" ]; then
    say "left $(tilde "$TARGET") alone: it is not this project's script"
  fi

  if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1 &&
     jq -e --arg cmd "$TARGET" '.statusLine.command == $cmd' "$SETTINGS" >/dev/null 2>&1; then
    cp -p "$SETTINGS" "$SETTINGS.bak-$STAMP"
    jq 'del(.statusLine)' "$SETTINGS" | replace_contents "$SETTINGS"
    say "removed statusLine from settings.json (backup: $(tilde "$SETTINGS.bak-$STAMP"))"
  fi

  # backups are named by timestamp, so the glob order is also the time order
  latest=""
  for f in "$TARGET".bak-*; do
    if [ -e "$f" ]; then latest=$f; fi
  done
  if [ -n "$latest" ]; then
    say "your earlier status line is kept in $(tilde "$latest")"
  fi
  if [ -f "$CONFIG" ]; then
    say "kept $(tilde "$CONFIG"); delete it if you are done for good"
  fi
}

case "$ACTION" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
esac
