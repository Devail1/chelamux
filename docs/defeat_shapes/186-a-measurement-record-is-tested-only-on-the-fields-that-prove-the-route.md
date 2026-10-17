## 186. A measurement record is tested only on the fields that prove the route, never on the fields the report is built from

**Assertion form:** a probe function sends something, then returns one dict of a dozen
measured fields (delivered, acked, latencies, row type, stranded-on-prompt, what the model
said…) that a driver prints as JSON lines and a doc turns into its table. The test stubs the
send paths, asserts the right one was called and that `delivered`/`acked` are true, and stops.
Every other key is present in the dict, so the record *looks* exercised.

**Mutation that defeats it:** replace any one unasserted field's expression with a constant
(`"stranded_on_prompt": pid in pane(win).split("❯")[-1]` → `"stranded_on_prompt": False`).
The routing and the two headline booleans are computed independently of that field, so the
suite stays green while the doc's column reports the constant for every row.

**Guard form that survives:** assert the WHOLE record with `==` against a literal, from a
fixture where each field takes a distinguishable non-default value (a fake clock so the
latencies are exact, a reply that names one colour and not the other, a pane where the probe
id appears both above and after the last prompt marker), plus a second fixture where every
field takes its other value (nothing delivered, clean prompt). A whole-dict `==` also goes red
on a field that is added or dropped, which per-key asserts never notice.

**Found:** CMX-186 rework round 1 (2026-10-17), PR #732 — `scripts/spikes/telegram_channels/
run_spike.py`'s `probe()`. `test_probe_routes_by_transport` asserted the transport used and
`delivered`/`acked`; the judge constant-folded `stranded_on_prompt` and the full suite stayed
green. Closed by `test_probe_reports_every_field` / `test_probe_reports_a_lost_message_and_a_clean_prompt`.
