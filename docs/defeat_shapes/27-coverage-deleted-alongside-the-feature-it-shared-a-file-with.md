## 27. Coverage deleted alongside the feature it shared a *file* with

**Assertion form:** a PR deletes a whole view/feature and, with it, that view's test file —
reasonable, since the view's OWN code is gone too. But some of the production code the
deleted view used was never exclusive to it: another surviving surface (a shared renderer,
a shared helper) imports the same module and is explicitly called out — in the PR's own
summary or the surviving file's header comment — as proof that module "survives, still
used verbatim." The deleted test file, however, held the *only* guards on branches of that
shared module the surviving caller's own tests never happen to exercise (a fixture that only
ever used one of the module's two code paths). Deleting the file deletes those guards too,
silently — the suite's pass count doesn't even move, because nothing was left half-covered
in a way a diff of test *counts* would show.

**Mutation that defeats it:** corrupt the surviving module's unexercised branch (the one only
the deleted view's tests drove). Nothing in the remaining suite reaches it, so the corruption
ships clean — while the PR's own text claims that exact module "still works" for the
surviving caller.

**Guard form that survives:** when a PR deletes a test FILE (not just a test), list every
production symbol that file imported and tested, and for each one still referenced by
surviving code, check off that either (a) the deleted file's guards for the branches the
survivor actually exercises were re-homed into a surviving test file, or (b) an equivalent
guard already exists there. "The suite still passes at N tests" is not evidence — a file that
tested 8 branches of a 3-branch-shared, 5-branch-exclusive module and gets deleted whole
looks, in a pass-count diff, identical to a file that tested nothing the survivor needed.

**Found:** CMX-279 rework round 2 (2026-08-14), PR #350. `tests/knowledge_graph.test.mjs`
was deleted with the rest of the Knowledge view (CMX-279's five-view strip), but
`knowledge.js`'s `knMd`/`knInline` were kept — per the file's own header — because the Work
view's task-detail modal (`taskmodalmodel.js`/`taskmodal.js`) and `kanban.js`'s card titles
still call them verbatim. The surviving guard (`tests/taskmodal_model.test.mjs`'s exact-output
`briefHtml` test) only ever fed `knMd` a heading + an ORDERED (`1.`/`2.`) list + inline code —
no fixture anywhere contained a `-`/`*` bullet. The judge made a `-` run open `<ol class="kn-ol">`
while `closeList()` still emitted `</ul>` for it (mismatched tags on every bulleted brief in
the app) and the full suite — 3064 tests — stayed green. Closed by three new tests added
directly to `tests/taskmodal_model.test.mjs` (not a revived `knowledge_graph.test.mjs`, since
the Knowledge view itself is gone — the guard belongs with the surviving caller now) driving
`knMd` on a `-` run, a heading splitting a `-` run from a `1.` run, and a `-` run switching
directly into a `1.` run mid-document — restoring the three cases the deleted file's own guard
comments named.
