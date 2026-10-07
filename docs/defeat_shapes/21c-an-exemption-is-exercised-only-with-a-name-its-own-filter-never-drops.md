## 21c. An exemption is exercised only with a name its own filter never drops

**Assertion form:** a filter carries an allow-list exemption — `child_env` keeps a var when
`k in forward or not is_session_marker(k)`, so an operator-forwarded name survives the
session-marker strip. The test that "proves the forward list works" forwards a name that
this filter would have kept anyway: `HTTPS_PROXY` through `server_env`, which wraps
`child_env`. The proxy is dropped by a *different* filter (`server_env`'s hazard strip, with
its own `k in forward` copy), so the test is green and reads as "forwarding is honoured" —
but the exemption under test is never the thing standing between the name and the strip.
The same exemption existed a second time (`tmux_scrub_names`), and that copy WAS tested with
a forwarded marker, which made the pair look covered.

**Mutation that defeats it:** neuter the exemption in the copy whose test only forwards an
unfiltered name — `and (k in forward or not is_session_marker(k))` →
`and (False or not is_session_marker(k))`. A forwarded marker is now stripped, the
operator's deliberately-set `CLAUDE_CODE_MESSAGING_SOCKET` never reaches the child, and
every test stays green: the proxy-forward test never sends a marker through this clause.

**Guard form that survives:** for each copy of an exemption, drive it with a name that copy's
own filter WOULD drop — here a session marker in `CHELA_CHILD_ENV_FORWARD` through
`child_env` itself — and assert it survives; in the same test, show an unforwarded name of
the same kind IS dropped, and that the forwarded one is dropped once the forward list is
removed. The question to ask of every exemption test: "if I deleted the exemption, would this
name have been removed?" If not, the test is about a different filter.

**Found:** CMX-21 rework round 2 (2026-10-07), PR #604. The judge applied the mutation above;
5988 tests stayed green. Closed by
`test_child_env_keeps_a_session_marker_the_operator_forwards` in
`tests/test_env_leak_hardening.py`. The same round's other survivor (a dead-port short-circuit
whose only test stubbed the port probe to `False`, so `port in _DEAD_PORTS` never decided
anything) is shape 35, closed by driving the probe to `True`.
