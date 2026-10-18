// @ts-check
// Module boundaries for the dashboard frontend (CMX-171, Liav 2026-10-17), so the React code
// cannot regrow a 3,656-line nav.js. Adapted from mattpocock/skills `setup-ts-deep-modules`
// (MIT, https://github.com/mattpocock/skills): its rules, with two module roots instead of one.
//
// Each dir under src/features/ and src/shared/ is a deep module: its ROOT files (index.ts,
// Sidebar.tsx, ...) are its entry points; any subfolder (lib/, tests/) is private.

/** One module per immediate child of a root; `$1` names the module (features/x, shared/y). */
const MODULE = '^src/((?:features|shared)/[^/]+)/';
/** A module's private internals: anything inside one of its subfolders. */
const INTERNALS = '^src/(?:features|shared)/[^/]+/[^/]+/';
const TESTS = '^src/(?:features|shared)/[^/]+/tests/';

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: 'entrypoint-boundary-from-app',
      comment: "(1) Code outside every module imports a module's root files, never its subfolders.",
      severity: 'error',
      from: { pathNot: MODULE },
      to: { path: INTERNALS },
    },
    {
      // `^src/$1/` (the importer's own module) is what lets a module reach its own internals.
      name: 'entrypoint-boundary-across-modules',
      comment: "(1)+(2) A module's files import each other freely, other modules only via root files.",
      severity: 'error',
      from: { path: MODULE, pathNot: TESTS },
      to: { path: INTERNALS, pathNot: '^src/$1/' },
    },
    {
      name: 'tests-through-entrypoints',
      comment: '(3) Tests import entry points and their own tests/ fixtures, never internals.',
      severity: 'error',
      from: { path: `${MODULE}tests/` },
      to: { path: INTERNALS, pathNot: '^src/$1/tests/' },
    },
    {
      name: 'tests-folder-is-private',
      comment: '(3) Only tests may import test fixtures.',
      severity: 'error',
      from: { pathNot: TESTS },
      to: { path: TESTS },
    },
    { name: 'no-circular', comment: '(4) No cycles.', severity: 'error', from: {}, to: { circular: true } },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    // Type-only imports cross boundaries too (`import type`, which verbatimModuleSyntax makes common).
    tsPreCompilationDeps: true,
    tsConfig: { fileName: 'tsconfig.json' },
    enhancedResolveOptions: { extensions: ['.ts', '.tsx', '.js', '.jsx', '.json'] },
  },
};
