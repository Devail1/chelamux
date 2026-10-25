## 289. A later failure clause whose every failure fixture also trips an earlier one

**Assertion form:** a helper makes several git (or I/O) calls and folds every way they can
fail into one conservative early return — `if old is None or new is None or diff.returncode
!= 0: return None` — so "git cannot say" is never read as a positive answer. The suite has a
negative control per *input*: a bogus judged sha, a bogus head sha, a missing base branch,
each asserting `None`.

**Mutation that defeats it:** drop the last clause — `… or diff.returncode != 0:` → `… or
False:`. Every fixture stays green: a bogus sha that makes `git diff judged head` fail ALSO
makes the merge-base diff for that same sha fail, so `old is None` or `new is None` fires
first and the third clause is never the one deciding. With the clause gone, a failed `git
diff` (empty stdout) reads as "nothing moved", and the caller carries a verdict across a head
nobody compared.

**Why the existing negative controls don't catch it:** the fixtures vary the *input*, but the
three clauses are not independent in the input — one bad sha breaks every git call that
names it. Each fixture therefore exercises the earliest clause, never the last. A clause is
only load-bearing in the test suite if some fixture makes it the ONLY one that is true.

**Guard form that survives:** for each clause of a conservative `or`-chain, build one fixture
where that clause alone is true — usually by stubbing the earlier calls to succeed (here:
`judge._changed_files` patched to return a path list) while the real call under the clause
fails. Assert the conservative result.

**Found:** `chela.verdictcarry.moved_pr_paths` (CMX-289, PR #878, round 1). Closed by
`test_a_head_pair_git_cannot_diff_is_unknown_even_when_both_pr_diffs_read`. The same round
also found a flag round-tripped through a persisted record (`held_out=o.held_out` →
`held_out=False`) that was only ever recorded as `False` — [[7b|shape 7b]] in a new place;
closed by `test_a_held_out_experiment_rerun_from_the_prior_round_stays_held_out`, which
records a held-out experiment, re-runs it next round and asserts it is neither listed nor
named in the PR comment.

**See also:** [[326|shape 326]] — one null-guard per operand; this is the same gap when the
operands share a cause; [[7c|shape 7c]] — a check repeated at two stages tested only past the
later one.
