### Changed

- **Window names change only to resolve a duplicate, and a manual rename always wins.** The
  reconcile loop no longer rewrites a unique window name (`shell-N`, a spawn name, anything)
  to its cwd basename — a freshly spawned `shell-1` used to become `tradeplan-2` within one
  tick. When two windows share a name, the newer one (higher window index) gets a
  collision-safe `-N`; the older keeps it. A dashboard rename now flags the window
  `@chela_manual_name`, and a flagged name is never renamed: it keeps the name over a
  non-manual duplicate, and two colliding manual names are both left alone with a warning.
  The dashboard Start button no longer renames the window either; it only locks it. A raw
  `tmux rename-window` is not detected as manual (rename through the dashboard for that),
  though a unique name is never rewritten regardless.
- **The sidebar row and pane header lead with Claude's session title.** Label order is
  manual name > Claude's session title > window name; the short window name rides
  secondary (the pane subtitle, the row's hover). No title yet ⇒ the window name — the old
  cwd-basename fallback is gone. (CMX-62)
