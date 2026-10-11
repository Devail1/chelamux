## 84b. A spy that flattens what it records cannot see the dimension the mutation moves

**Assertion form:** the CMX-84 `claude` shell wrapper was proven by running it in a real `bash`
against a stub `tmux` that logged `echo "$*"`, and a `_send` spy that recorded only the TEXT
typed (`lambda target, text: sent.append(text)`). Every assertion compared the logged strings
exactly — they looked airtight.

**Why that's a gap:** each spy discarded one dimension before any assertion could read it.
`"$*"` joins arguments with spaces, so `[my  proj]` (one argument) and `[my][proj]` (two)
log the same line — an unquoted `$_chela_n`, `$@` on a pass-through path, or a `for a in $@`
skip scan all word-split and still log identically. The text-only `_send` spy never saw WHERE
the keystrokes went, so typing the definition into the wrong window was invisible. And a third
corruption is argv-identical by construction: the wrapper calling ITSELF (`claude …` instead of
`command claude …`) lands the same argv, because the inner call sees `--remote-control` and
passes straight through.

**Mutation that defeats it:** unquote `"$_chela_n"` in the mark or the launch; unquote `"$@"`
on the skip or positional path; `for _chela_a in $@`; `_send(wid or target, …)`; or drop
`command` from the final launch. All green against the flattening spies.

**Guard form that survives:** record every dimension the code under test chooses — the stub
logs each argument bracketed (`printf "[%s]" "$a"`), the `_send` spy keeps `(target, text)`
and the test asserts the target. Feed values that only split if unquoted (a live window name
and argument values with double spaces, an argument VALUE containing ` -p `) down EVERY path,
not just the main one. For a self-recursion that is argv-identical, make it observable rather
than inferring it: run with `FUNCNEST=1`, so a nested call of the function fails.
Related: [[84|shape 84]] (the same PR's first round).

**Found:** CMX-84 rework round 2 (2026-10-11), PR #646 — closed by
`test_a_live_name_with_spaces_stays_one_argument`,
`test_a_flag_inside_an_argument_value_does_not_skip`, the spaced skip-path cases of
`test_non_interactive_or_already_flagged_claude_is_unchanged`, the target assertion in
`_new_shell`, and `FUNCNEST=1` in `_type`.
