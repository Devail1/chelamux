## 62b. A disjunctive exemption is tested only with a fixture that satisfies every clause at once

**Assertion form:** an exemption predicate is an `or` of independent reasons to spare
something (`name.startswith("judge-") or "/" in name` — "a judge window, or a dispatch
branch window"), and the test that proves the exemption feeds it a fixture built to look
like the real thing — `judge-x/cmx-1`, which is a judge name AND contains a `/`. The test
asserts "never renamed" and is green. A sibling sits alongside it: a set-membership
exemption (`name in NEVER_MANAGE`) whose set is empty by default, so no fixture ever
reaches it at all.

**Mutation that defeats it:** delete either clause of the `or`. The fixture still
satisfies the surviving one, so the exemption still fires and the window is still spared —
same output. Deleting the empty-set clause is invisible for the simpler reason that no
fixture ever names a member.

**Guard form that survives:** arm each clause ALONE, one fixture per clause, chosen so that
it satisfies exactly that clause and none of the others (`judge-cmx-5` — no slash;
`x/cmx-2-fix` — no `judge-` prefix; and a monkeypatched `NEVER_MANAGE={"pinned"}` with a
name neither other clause matches). Then each deletion leaves one fixture with no reason to
be spared, and its test goes red. The same goes for a disjunctive TRIGGER
(`auto != "0" or allow != "0"` → lock): a fixture with both halves live hides the deletion
of either; one per half (`auto=1, allow=0` and `auto=0, allow=1`) does not.

**Why this is distinct from [[55|shape 55]] and [[66|shape 66]]:** shape 55 is a conjunctive
(`and`) entry gate whose bound clause is never driven false alongside an armed downstream
signal. Shape 66 is a two-axis predicate whose negative control covers only one axis. This
is the positive side of an `or`: the fixture is realistic precisely because it satisfies
every clause — and that realism is what makes each clause individually redundant to the test.

**Found:** CMX-62 rework round 1, PR #629. `chela/agent_manager.py`'s
`reconcile_window_names` spares dispatch/judge windows via `_orchestrated_name` and the
`NEVER_MANAGE` set; the only test used `judge-x/cmx-1` + `x/cmx-2-fix` and never populated
the set. A held-out mutation survived the round; the per-clause tests in
`tests/test_agent_manager_naming.py` (`…judge_window_without_a_slash`,
`…dispatch_window_without_a_judge_prefix`, `…honours_never_manage_on_a_duplicate`,
`…locks_when_only_allow_rename_is_still_on`) close this shape alongside the other
unarmed arms that round found (a three-way duplicate for the reserved-name set, a `"0"`
manual flag, a failed `display-message`, `ai_title` vs `session_name` both present).
