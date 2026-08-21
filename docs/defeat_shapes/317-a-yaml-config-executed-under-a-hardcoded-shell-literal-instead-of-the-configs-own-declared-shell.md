## 317. A YAML config executed under a hardcoded shell literal instead of the config's own declared shell

**Assertion form:** CMX-317 stopped comparing the ref-state step's `run:` TEXT and started
executing it — a real improvement, closing the source-constant-vs-rendered-value gap
[`314`](314-a-removed-branch-naming-check-is-pinned-only-on-the-one-step-it-used-to-live-on.md)
already named for the block's own text. But the harness runs that block under
`_GITHUB_RUN_SHELL = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]` — a Python literal
standing in for "whatever shell GitHub actually invokes this `run:` step under" — rather than
reading it from `ci.yml`'s own `defaults.run.shell` (workflow / job / step, in GitHub's
precedence order). The literal is *usually* correct, because that is GitHub's default shell
for a `run:` step with no `defaults:` override anywhere in the file — but "usually correct"
and "derived from the rendered value" are different properties, and only the second one is a
guard.

**Mutation that defeats it:** add a root-level `defaults:` block to the workflow, switching
every job's `run:` steps to GitHub's CUSTOM-shell form:

```diff
   pull_request:

+defaults:
+  run:
+    shell: bash {0}
+
 jobs:
```

`bash {0}` (an explicit shell invocation with a script-file placeholder) is GitHub's syntax
for *opting out* of its own default flags — unlike the bare shell name `bash`, which GitHub
expands to `bash --noprofile --norc -eo pipefail {0}` itself
(https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions#defaultsrun).
The custom form runs the script with none of that: no `-e`, no `pipefail`. In real CI, the
ref-state block's middle line (`[ -n "$ref" ] && [ "$ref" != "HEAD" ]`) failing no longer
aborts the step — its exit status is silently discarded, and the step's actual result becomes
line 3's `git rev-parse --verify --quiet origin/dev`, which succeeds under `fetch-depth: 0`
regardless of what HEAD names. A DETACHED HEAD — the exact state CMX-305 added the block to
catch — is accepted, and the CMX-301 defeat-shape numbering guard skips silently again.

This root-level key changes nothing `tests/test_ci_workflow.py` looked at before this entry:
`workflow[True]` (the trigger block), `set(workflow["jobs"])` (the job set), and the whole
`jobs.test` mapping — `runs-on`, `strategy`, and the complete pinned `steps` list — are all
byte-identical before and after. Applied by the judge to a throwaway checkout of this PR's
head, `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3312 passed, 0 failed, 0
error(s)) through the mutation.

**Why this slips through:** CMX-317's own module comment is explicit that `-e` is
load-bearing — "what makes lines 2 and 3 of this block assertions at all rather than
statements whose exit codes are discarded" — and even adds a dedicated test
(`test_the_ref_state_block_is_executed_under_githubs_own_shell_flags`) to prove `-e` is in
force *in the harness*. That test is airtight on its own terms: the harness's `-e` really is
in force. It just isn't a statement about CI's `-e`, because the harness's shell flags are a
literal, not a read of `ci.yml`. This is
[`05-asserting-a-source-constant-instead-of-the-rendered-value`](05-asserting-a-source-constant-instead-of-the-rendered-value.md)
recurring one level up from where CMX-317 closed it for the block's TEXT: the fix that closes
a source-constant gap for one property (what the block *says*) doesn't automatically close
the identical gap for a different property one layer out (what shell it *runs under*) — each
needs its own derivation from the rendered artifact, and fixing one is easy to mistake for
having fixed the shape itself.

**Guard form that survives:** pin the workflow's ROOT key set — `{"name", the on-block key,
"jobs"}` today — the same way `17-...` (`test_the_workflow_has_exactly_one_job`) pins the job
id set and `18-...` (`test_the_job_mapping_is_pinned_exactly`) pins the job's own keys,
without re-pinning content that's already pinned exactly elsewhere (the trigger block, the
job mapping). A new root key — `defaults:`, `env:`, `permissions:`, `concurrency:`, or
anything not yet imagined — is invisible to every test that resolves through
`workflow["jobs"]` or `workflow[True]` alone, so it has to be caught one level further out,
at the root, or not at all. (Deriving `_GITHUB_RUN_SHELL` from `workflow.get("defaults", {})`
directly — reproducing GitHub's own shell-precedence and custom-shell semantics — would also
close this, but is a second, parallel interpreter of the same YAML the workflow itself
doesn't need; the key-set pin closes the same gap more cheaply, in the same shape already
established one level down.)
