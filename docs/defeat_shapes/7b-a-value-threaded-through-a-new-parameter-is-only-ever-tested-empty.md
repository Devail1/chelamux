## 7b. A value threaded through a new optional parameter is only ever tested empty, so dropping it at a call site is invisible

**Assertion form:** CMX-7 threaded the operator's approved domains into the web sidecar's
allow-list: `run()` → `_web_steps(sid, uid, gid, domains)` → `web_proxy_run_argv(...,
extra_allow)` → `web_allow(extra)`. Each new parameter defaulted to `()`. The leaf was
tested directly (`web_allow([...])` and `web_proxy_run_argv(..., ["jobs.example.com"])`
both asserted the merged `CHELA_WEB_ALLOW`). The launcher was tested end to end too
(`test_run_brings_up_the_web_sidecar_only_in_web_mode`, and a relaunch-on-approval test).
But every `run()` fixture had **no approved domain** and **no `CHELA_SHARE_WEB_ALLOW`**, so
the value reaching the call site was `()`, the parameter's own default.

**Mutation that defeats it:** `steps += _web_steps(sid, uid, gid, domains)` →
`steps += _web_steps(sid, uid, gid)`. The same thing happened with the relaunch branch
`if net == NET_WEB and new_domains != domains:` → `if False and …`. The full suite stayed
green both times. Passing `()` explicitly and omitting it give the same argv, so the only
test that drove `run()` couldn't tell a dropped argument from a passed one. The leaf tests
pass a literal list, so they never go through the call site at all.

**Guard form that survives:** test each hop where a value is threaded in with a **non-default
value already in force at that hop**, and assert it at the far end. Here that means: a domain
approved *before* `run()` (the sidecar's first `docker run` carries
`docs.python.org,jobs.example.com`), and a domain approved *during* the guest's first run (the
sidecar is removed and restarted, and the second `docker run` carries the new list). A leaf
test with a literal argument proves the leaf. Only a caller test whose fixture makes the
argument differ from its default proves the wiring. The same fix closed the `0o600` store
mode in the same PR: the docstring claimed it, and nothing ever `stat`ed the file under
`umask(0)`.

Found by the judge on PR #591 (CMX-7, round 1). Guards:
`tests/test_share_requests.py::test_a_web_session_starts_its_sidecar_with_domains_already_approved`,
`::test_a_domain_approved_mid_session_restarts_the_sidecar_with_it`.
