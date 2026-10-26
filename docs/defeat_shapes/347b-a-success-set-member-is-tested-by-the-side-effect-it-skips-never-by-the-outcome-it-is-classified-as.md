## 347b. A success-set member is tested by the side effect it skips, never by the outcome it is classified as

**Assertion form:** one status word (`"already"`) is read in two places with two different
meanings. A narrow tuple (`("set", "struck", "created")`) decides whether a follow-up write
happens, and a wider one (`LANDED = ("set", "already", "struck", "created")`) decides the
outcome that gets recorded (`accepted` or `failed`). The test drives the word through
(`fake.transition = lambda ...: "already"`) and asserts the returned results and that **no
follow-up write** happened. Those are the effects of the narrow tuple. Nothing reads back the
outcome the wide tuple produced.

**Why it doesn't guard:** removing the word from the wide set changes only the recorded
outcome (`accepted` becomes `failed`), and no assertion looks at the record. The visible
behaviour is identical under both sets: the same result string comes back, the same writes
are skipped. Here the record is the product, since the trial's acceptance rate is computed
from `log.jsonl`, so every approved no-op would silently count as a failure.

**Mutation that defeats it:** (CMX-347)

```diff
- LANDED = ("set", "already", "struck", "created")
+ LANDED = ("set", "struck", "created")
```

**Guard form that survives:** for each member of a classification set, assert the classification itself: read the
logged `outcome` (and `result`) for that word, not only the behaviour a sibling set controls.
When two sets overlap but differ by one member, give that member a test that reads both
consequences: the skipped write, and the recorded outcome.

**Related:** [[342|shape 342]] (two quantities that coincide in every fixture). The other two
guards that survived on the same PR were that shape (the cited evidence vs. the whole digest)
and [[430b|shape 430b]] (a `load_workflow` fake that ignored the path it was given).
