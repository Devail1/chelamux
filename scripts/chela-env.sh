#!/usr/bin/env bash
# Source the chela env file. `. scripts/chela-env.sh` — not executed directly.
#
# $CHELA_DIR/chela.env is the ONE place a chela install's config is written down (see
# examples/chela.env). A process manager must not carry its own copy: two places is how
# CHELA_TMUX_SESSION ended up naming a tmux session that no longer existed — in three PM2
# `env:` blocks at once, for a day, silently.
#
# Semantics mirror chela/config.py's loader exactly, so a value resolves the same whether
# it is read by Python or by a shell script: an already-exported variable WINS over the
# file (an override stays possible), the file beats the built-in defaults, and `KEY=value`
# / `export KEY=value` / `# comment` / one layer of surrounding quotes is the syntax.

CHELA_DIR="${CHELA_DIR:-$HOME/.chela}"
export CHELA_DIR

_chela_source_env() {
  local file="$1" line key value
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"          # ltrim
    case "$line" in '' | '#'*) continue ;; esac
    line="${line#export }"
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    key="${key// /}"
    [ -n "$key" ] || continue
    case "$value" in
      \"*\") value="${value#\"}" ; value="${value%\"}" ;;
      \'*\') value="${value#\'}" ; value="${value%\'}" ;;
    esac
    [ -n "${!key+set}" ] || export "$key=$value"      # the environment wins
  done < "$file"
}

_chela_source_env "$CHELA_DIR/chela.env"
# Secrets live in their own file (chmod 600) — chela.env is the pasteable one, and a bot
# token has no business in it. Only a service that needs one will find anything here.
_chela_source_env "$CHELA_DIR/secrets.env"
