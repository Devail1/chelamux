"""chela CLI entry point.

This is the early scaffold: `chela status` proves tmux-native discovery works.
`run`, `dispatch`, and the rest land as the core is ported in.
"""
from __future__ import annotations
import argparse

from chela import discovery
from chela.config import TMUX_SESSION


def cmd_status(args) -> None:
    """List the agent windows chela can see in the tmux session."""
    windows = discovery.get_all_windows()
    if not windows:
        print(f"No windows found in tmux session '{TMUX_SESSION}'.")
        print("Is the session running? Override the session with CHELA_TMUX_SESSION.")
        return
    print(f"Agents in tmux session '{TMUX_SESSION}':\n")
    for name, wid in sorted(windows.items()):
        cwd = discovery.get_window_cwd(name) or "?"
        print(f"  {name:<24} {wid:<6} {cwd}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chela",
        description="A tiny control plane for a fleet of Claude Code agents on tmux.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="List discovered agent windows")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
