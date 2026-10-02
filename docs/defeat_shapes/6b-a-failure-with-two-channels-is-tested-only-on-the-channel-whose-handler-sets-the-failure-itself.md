## 6b. A failure that arrives on two channels is tested only on the channel whose own handler sets the failure state

**Assertion form:** an operation can fail two ways — the call **raises** (a transport error,
a rejected `fetch`) or it **returns** a refusal (GraphQL `{"success": false}`, an HTTP 4xx/5xx
with `{ok: false}`). The code handles each on its own line: the `except`/`catch` branch sets
the failure state itself (`ok = False`; `return null`), and the normal path computes it from
the reply (`ok = bool(reply.get("success"))`; `if (!resp.ok || !data.ok) return null`). The
test drives exactly one channel and asserts the failure outcome (a warning, the form kept).

**Mutation that defeats it:** corrupt the line of the channel the fixture never drives.
CMX-6 had both halves at once:

- Python — the fixture's relation stub only ever **raised** `LinearError`, so the `except`
  branch's own `ok = False` produced the warning. `ok = bool(…get("success"))` → `ok = True`
  stayed green: no fixture ever reached that line with `success: false`.
- JS — the "keeps the form" test only fed an **HTTP 502**, whose `if (!resp.ok …) return null`
  kept the form. Deleting the `return null` from the `catch` (a rejected `fetch`, i.e. the
  network is down) let a network failure fall through to `_clearForm()` + `closeModal()` —
  the typed brief lost — and stayed green: no fixture ever rejected the fetch.

**Why the existing test doesn't catch it:** "a failed X becomes a warning" reads as one
invariant, but the code implements it twice. A test of one channel proves only that channel's
branch; the other branch's failure-setting statement is unexecuted under the suite.

**Guard form that survives:** enumerate the failure channels from the code, not from the
spec sentence, and parametrize the same outcome assertion over each — raise, reply
`success: false`, reply with the payload missing; HTTP error with JSON, HTTP error with a
non-JSON body, a 200 that says `ok: false`, a rejected `fetch`. Assert the whole kept state
(every field, the form still open, no success-side effect such as the queue refresh), so a
fall-through to the success path goes red whichever branch it leaks from.
