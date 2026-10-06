# mkwin.sh N — (re)build the PRIVATE tmux server with N fake agent windows.
set -e; . "$(dirname "$0")/env.sh"; N=$1
tmux kill-server 2>/dev/null || true; sleep 0.3; mkdir -p "$H/cwds"
tmux -f /dev/null new-session -d -s chela -n __main__ -x 200 -y 50
for i in $(seq 1 "$N"); do mkdir -p "$H/cwds/agent$i"
  tmux new-window -d -t chela: -n "agent$i" -c "$H/cwds/agent$i" "sh -c 'python3 $HERE/fake_claude.py; true'"
done
echo "$N windows on $(tmux display -p '#{socket_path}')"
