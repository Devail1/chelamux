# Sourced (bash or zsh) into a chela window's fresh shell by chela.spawn.spawn_window
# while Remote Control is on (CMX-84, CMX-269): `. claude-rc.sh <window name>`.
#
# Defines `claude` so a launch the human TYPES here gets `--remote-control <window name>`
# and marks the window with @chela_rc_name (chela.rc_rename.PUSHED_OPTION), exactly as
# chela.spawn._add_remote_control + chela.rc_rename.mark_launched do for chela's own launch:
#   * a first argument that is not a flag (a prompt, a subcommand such as `mcp`) leaves the
#     argv untouched - the flag's OPTIONAL value would otherwise swallow it;
#   * an argument already naming its own --remote-control, or a non-interactive form
#     (-p/--print, help, version), leaves it untouched;
#   * otherwise the window's LIVE name (#W) goes right after `claude`, falling back to the
#     name it opened with ($1 here) when tmux cannot answer.
# The window is $CHELA_WID (exported on the same line that sources this), else this pane.
# `command claude` reaches the real binary, so the wrapper never recurses; ksh-style
# `function claude {` so a user's own `claude` alias is neither clobbered nor a syntax error.
_chela_rc_fallback=${1-}
function claude { local _chela_a _chela_n _chela_t; case "${1:-}" in ""|-*) ;; *) command claude "$@"; return;; esac
  for _chela_a in "$@"; do case "$_chela_a" in
    --remote-control|--remote-control=*|-p|--print|-h|--help|-v|--version) command claude "$@"; return;; esac; done
  _chela_t=${CHELA_WID:-${TMUX_PANE:-}}
  _chela_n=$(command tmux display-message -p -t "$_chela_t" '#W' 2>/dev/null)
  [ -n "$_chela_n" ] || _chela_n=$_chela_rc_fallback
  command tmux set-window-option -t "$_chela_t" @chela_rc_name "$_chela_n" >/dev/null 2>&1
  command claude --remote-control "$_chela_n" "$@"; }
