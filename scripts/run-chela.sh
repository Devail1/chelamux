#!/usr/bin/env bash
# The one way a chela service is started: source the env file, then exec the CLI.
#
#   scripts/run-chela.sh run          # the daemon
#   scripts/run-chela.sh dashboard    # the web dashboard
#   scripts/run-chela.sh telegram     # the Telegram bridge
#
# WHY a launcher instead of a process manager's `env:` block: config that lives in two
# places drifts. chela's did — three PM2 `env:` blocks each carried their own copy of
# CHELA_TMUX_SESSION, and all three still named the OLD tmux session a day after the
# rename. Whatever starts a service (PM2, systemd, a shell) should carry NO config of its
# own; it points here, and here reads $CHELA_DIR/chela.env — the only place a value is
# written down. See examples/ecosystem.config.js.
#
# `chela` also sources chela.env itself (chela/config.py, at import), so a bare
# `chela status` in a plain shell is configured identically. This script exists for the
# two things that import cannot do: put the config in the environment of a whole process
# tree (ttyd, the agents it spawns), and scrub the tmux leak below.
set -euo pipefail

# shellcheck source=scripts/chela-env.sh
. "$(dirname "$0")/chela-env.sh"

# ⚠️ LOAD-BEARING. config.current_session() resolves $CHELA_TMUX_SESSION → else the
# session that owns $TMUX_PANE → else "chela". That middle step is right for an agent
# living in a pane and WRONG for a service: a TMUX/TMUX_PANE inherited from the shell
# that ran `pm2 start` makes the service silently target whatever session that pane was
# in — a `webterm_*` MIRROR, in the case that bit us. A service must never inherit one.
exec env -u TMUX -u TMUX_PANE chela "$@"
