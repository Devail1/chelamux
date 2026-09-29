## 402. A "nothing is inherited" guard probes only the variables it thought of, so inheriting any other one survives

**Assertion form:** a function builds an environment FROM SCRATCH on purpose (a demo fleet, a
sandboxed child process). Its guard plants a few named probes in `os.environ`, such as `TMUX`,
`TMUX_PANE` and a made-up `CHELA_SECRET_PROBE`, then asserts that each probe is absent from the
built env. The property being claimed is universal ("nothing but `PATH` and `LANG` comes
through"). The fixture checks it only for the three keys someone happened to write down.

**Mutation that defeats it:** inherit one more variable that is not in the probe list, such as
`"LC_ALL": os.environ.get("LC_ALL", "")`, or copy a prefix-filtered slice of the parent env.
Every probe is still absent, so the suite stays green. The judge's "demo env is FROM SCRATCH"
experiment on CMX-402 SURVIVED against exactly this fixture.

**Why this is distinct from [[73|shape 73]]:** shape 73 is one fixture VALUE missing the
character that tells two helpers apart. Here the fixture's KEY SET is a small sample of an
unbounded domain (every variable the operator's shell might carry), and the claim is about the
whole domain.

**Guard form that survives:** make the probe cover the whole domain. Overwrite EVERY variable
already in `os.environ` (except the ones the function is allowed to read) with a sentinel value
such as `probe-<KEY>`. Also add the dangerous names that may not be set on the test machine
(`GH_TOKEN`, `ANTHROPIC_API_KEY`, `SSH_AUTH_SOCK`, …). Then assert that no sentinel appears in
any value of the built env. Do not compare built values with `os.environ` by equality. A
legitimately computed value can coincide with the test runner's own env: in the CMX-402 rework,
conftest's `CHELA_DIR` equalled the demo's, and that check false-failed.

**Found:** CMX-402 rework round 1 (2026-09-30), judge review of PR #553.
`tests/test_public_media.py::test_demo_fleet_env_is_temp_and_from_scratch` guarded
`scripts/demo/fleet.py`'s `demo_env`. Closed by probing every variable, plus a named-danger
list. Negative controls: inheriting all of `os.environ`, inheriting just `LC_ALL`, and flipping
`GIT_CONFIG_NOSYSTEM` each turn the test red.
