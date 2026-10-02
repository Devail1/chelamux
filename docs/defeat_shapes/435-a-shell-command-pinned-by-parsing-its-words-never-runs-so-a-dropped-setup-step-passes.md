## 435. A shell command pinned by PARSING its words never runs, so a dropped setup step (`mkdir -p`) still passes

**Assertion form:** the guest container's entry is one `sh -c` string —
`printf seed > ~/.claude.json && mkdir -p ~/.claude && printf creds > ~/.claude/.credentials.json && exec claude`.
The tests `shlex.split` it and assert on the words: the credentials JSON sits two words before
`~/.claude/.credentials.json`, the seed parses, the last two words are `exec claude`.

**Why that's a gap:** every one of those assertions is about *what the string says*, none about
*what it does when run*. The guest's HOME is a fresh tmpfs with no `.claude` directory, so the
`mkdir -p` is load-bearing: without it the redirect fails, the `&&` chain aborts, and Claude never
starts. Word-level parsing can't see that — the redirect target and payload are still right there.

**Mutation that defeats it:** delete `mkdir -p {GUEST_HOME}/.claude && ` from `guest_entry`.
Every parse-based assertion still holds.

**Guard form that survives:** execute the entry with `sh -c` against an EMPTY temp HOME (the
`GUEST_HOME` literal rewritten to it) and a stub `claude` on PATH that touches a marker; assert
exit 0, the marker exists, and the credentials file was written. Under the mutation the redirect
fails and the marker is never created.

**Found:** CMX-435 rework round 1 (2026-10-02), PR #586 — closed by
`test_the_entry_runs_in_an_empty_home_and_reaches_claude`.
