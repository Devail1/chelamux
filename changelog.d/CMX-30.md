### Fixed

- **Dispatched agents and judges show their context bar and cost again.** The statusLine
  hook wrote `context/<window name>.json`, so a window named `<org>/cmx-N-<slug>` pointed
  at a subdirectory that does not exist, and since 2026-10-02 every dispatched window cached
  nothing. The file is now keyed by `chela/cachekey.py`, the one mapping both the hook and
  `chela.context` use. A failed cache write now exits non-zero and appends to
  `$CHELA_DIR/statusline-errors.log` instead of failing silently. (CMX-30)
- **The Cost tab's Today / 7d / 30d views list only agents that ran in the period.** An
  agent with no snapshot inside the window no longer gets a $0.00 row. A live session with
  no reported cost shows `—`, and a real $0.00 is no longer turned into unknown. (CMX-30)
