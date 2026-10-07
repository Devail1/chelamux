# mkcopy.sh [SRC] — copy a chela dir (default ~/.chela) minus secrets, sockets, worktrees;
# write a safe chela.env (no dispatch, no notify, no relay, no restore-resume); disable
# every scheduled task in the copy so the harness daemon never prompts an agent.
set -e; . "$(dirname "$0")/env.sh"; SRC=${1:-$HOME/.chela}
rm -rf "$CHELA_DIR"; mkdir -p "$CHELA_DIR"
rsync -a --exclude worktrees --exclude socks --exclude 'share-*' --exclude '*.env' \
  --exclude 'secrets*' --exclude 'judge-*' --exclude '*.sock' --exclude '*.lock' \
  --exclude collab_id --exclude dashboard.port --exclude 'chela.env*' --exclude 'ecosystem*' \
  --exclude 'run-*.sh' --exclude agent_terminals.json "$SRC/" "$CHELA_DIR/"
printf '%s\n' CHELA_TMUX_SESSION=chela CHELA_TERMINALS_ENABLED=true CHELA_IGNORE_WINDOWS=__main__ \
  CHELA_DISPATCH_WORKFLOWS= CHELA_RESTORE_RESUME=false > "$CHELA_DIR/chela.env"
python3 - "$CHELA_DIR/scheduler.db" <<'P'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for (t,) in c.execute("select name from sqlite_master where type='table'"):
    if "enabled" in [r[1] for r in c.execute(f"pragma table_info({t})")]:
        print(t, c.execute(f"update {t} set enabled=0").rowcount, "schedule(s) disabled")
c.commit()
P
