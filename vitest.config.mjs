// CMX-216: the dashboard's tests/*.test.mjs suites, run by Vitest (they ran under `node --test`
// before). pytest runs each file on its own through tests/test_js_suites.py.
// The React frontend has its own config in chela/dashboard/frontend.
import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        include: ['tests/*.test.mjs'],
        // A fresh process per file, so each suite gets the fresh module graph and globals
        // `node --test` gave it (most suites install a jsdom window on globalThis).
        pool: 'forks',
        environment: 'node',
        // node --test had no per-test timeout; pytest's 120 s per file is the real bound.
        testTimeout: 60_000,
        hookTimeout: 60_000,
    },
});
