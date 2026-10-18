## 176b. A preset is proven by a violation its weaker sibling also catches

**Assertion form:** shape 176's fix, applied: a test runs ESLint over a tree with a
deliberate `const x: any` and asserts `@typescript-eslint/no-explicit-any` fires, so
"ESLint applies typescript-eslint `strict`" reads as covered. Next to it, the gate tools are
run by their binaries with the test's own flags and a throwaway `tsconfig.json`.

**Mutation that defeats it:** `...tseslint.configs.strict]` → `...tseslint.configs.recommended]`.
`no-explicit-any` is in BOTH presets, so the fixture still yields exactly the expected
finding. The same holds for every config the test bypasses: drop `src` from the real
tsconfig's `include`, or turn `"typecheck": "tsc --noEmit"` into `"tsc --noEmit || true"`,
and nothing that runs the binary directly over its own tsconfig can see it.

**Why it looks covered:** the rule set is graded by a violation, and the violation is caught.
But a preset is a SET; the only violations that tell `strict` from `recommended` are the ones
in their difference. And the CI job runs `pnpm <script>` over the real config, not the tool
with the test's arguments.

**Guard form that survives:** grade each gate the way CI runs it (`pnpm <script>` in a copy
of the package with a committed fixture tree overlaid) and give every claimed setting a
fixture only THAT setting rejects: `no-non-null-assertion` for `strict` (not in
`recommended`), a `.ts` file for `files: ['**/*.{ts,tsx}']`, a type-only import without
`type` for `verbatimModuleSyntax`, a root `vite.config.ts` error for `include`. Assert the
exit code AND the exact findings.

**Found:** CMX-176 judge round 2, PR #751. Fixed in `tests/test_frontend.py` (the
`pnpm`-run gate tests over `tests/fixtures/frontend_gates/`).
