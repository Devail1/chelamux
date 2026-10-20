// CMX-217: runs ONLY assert_closed_run_lane.mjs, the JS half of
// tests/test_dispatcher_worktree_gc.py's end-to-end fixture (its inputs come in the env).
// Not a *.test.mjs, so the dashboard suite run (vitest.config.mjs) never picks it up alone.
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        root: fileURLToPath(new URL('../..', import.meta.url)),
        include: ['tests/js_helpers/assert_closed_run_lane.mjs'],
        pool: 'forks',
        environment: 'node',
        testTimeout: 30_000,
    },
});
