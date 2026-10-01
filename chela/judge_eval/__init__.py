"""📏⚖️ THE JUDGE EVAL — an offline yardstick for the judge's experiment DESIGN (CMX-408).

The judge has two halves (see ``chela/judge.py``'s module docstring). The mechanical half —
apply the mutation, prove it applied, run the suite, a survivor blocks — is deterministic
and already tested. The LLM half, choosing WHICH mutations to try, is a grader nobody had
measured: the operator felt it was "too harsh" (#529, #546 and #547 went 4-5 rounds on
test-only findings), and CMX-405's risk levels now change the judge prompt by level. This
package is how a change to that prompt gets MEASURED instead of felt.

⛔ It runs ONLY the experiment-design step — the live ``JUDGE_PROMPT`` + ``RISK_GUIDANCE``,
rendered for a risk level, and the model producing its experiments JSON — against a
read-only copy of a PR's tree. It NEVER runs a test suite, never touches ``~/.chela``,
never talks to the daemon, and never changes ``chela/judge.py``'s behaviour.

The three scores, each from the cheapest grader that can produce it:

* **recall** (programmatic) — does a proposed experiment change the lines a known REAL
  weakness lives on? Targets are SEEDED regressions from history (``data/seeds.jsonl``) and
  historical survivors a rubric labelled ``real``. See :func:`grade.target_hit`.
* **contrived rate** (programmatic first, then an LLM with CHECKABLE claims) — the share of
  proposed experiments no ordinary future edit would produce. See ``rubric.md``.
* **validity** (programmatic) — the experiment applies cleanly as a text edit, through the
  judge's OWN :func:`chela.judge.apply_mutation` and :func:`chela.judge.parse_check`.

The dataset is split train/test BY PR (:func:`dataset.split_for`); a test-split run prints
aggregates only, so a test case's failures can never be pasted into the prompt being tuned.
The full method is ``docs/JUDGE_EVAL.md``.
"""
