// ONE POLLER for /api/dispatcher: Dispatch, Kanban and the sidebar badges each used to fetch
// it on their own timer. Structural, over the sources. (CMX-178's Decisions inbox also calls
// `api('/api/dispatcher')`, but on a single click, not a timer, so it is allowed alongside the
// Work poll without reopening the one-poller property this file protects.)
//
// The view registry's own tests (add or remove a view by editing the registry alone; every
// view has its panel) moved with it to the shell: frontend/src/features/shell/tests (CMX-248).
//
// Run: pnpm exec vitest run  (or `uv run pytest -q`: tests/test_js_suites.py runs every .test.mjs)
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'js');
const src = f => readFileSync(join(JS_DIR, f), 'utf8');

// --- one dataset, one poller -------------------------------------------------

// CMX-233: the poll is features/work's scheduler lane (lib/poll.ts); static/js has none.
const SRC_DIR = join(JS_DIR, '../../frontend/src');
const srcFiles = dir => readdirSync(join(SRC_DIR, dir), { recursive: true })
    .filter(f => /\.tsx?$/.test(f) && !/\.test\./.test(f) && !f.includes('tests/')).map(f => join(dir, f));
const tsSrc = f => readFileSync(join(SRC_DIR, f), 'utf8');

test('exactly ONE module POLLS /api/dispatcher: features/work, as a scheduler lane', () => {
    // The Decisions inbox (frontend/src/features/decisions, CMX-178) also reads
    // /api/dispatcher, once per click-through, never on a timer: the next test.
    // The property this test protects is "no second POLLER", not "no other module
    // may ever call the endpoint".
    const legacy = readdirSync(JS_DIR).filter(f => /\.[jt]s$/.test(f)).filter(f => src(f).includes("api('/api/dispatcher"));
    assert.deepEqual(legacy, []);
    const owners = srcFiles('.').filter(f => tsSrc(f).includes("api('/api/dispatcher')")).sort();
    assert.deepEqual(owners, ['features/decisions/lib/inbox.ts', 'features/work/lib/poll.ts']);
    const poll = tsSrc('features/work/lib/poll.ts');
    assert.match(poll, /registerLane\('work', \(\) => pollWork\(\), 'detached'\)/);
    assert.ok(!poll.includes('setInterval'), 'the lane is the timer: no second one');
});

test('the Decisions inbox reads /api/dispatcher on a click, never on a poll timer', () => {
    const inbox = readFileSync(new URL('../chela/dashboard/frontend/src/features/decisions/lib/inbox.ts', import.meta.url), 'utf8');
    assert.ok(inbox.includes("api('/api/dispatcher')"),
        'the inbox must resolve a click-through against the dispatcher (CMX-178 rework)');
    assert.ok(!inbox.includes('setInterval('),
        'the inbox must not grow its own poll loop: one fetch per click only');
});

test('the Work renderers take a payload — they do not fetch one', () => {
    // The board, the shell, the Runs tables and the New-task form read the payload the poll
    // publishes (shared/store/work.ts); none fetches /api/dispatcher or runs a timer.
    const files = [...srcFiles('features/work').filter(f => !f.endsWith('poll.ts')), 'features/runs/lib/Runs.tsx', 'features/newtask/lib/NewTask.tsx'];
    assert.ok(files.includes('features/work/lib/Board.tsx'), `the board's source was not scanned: ${files}`);
    for (const f of files) {
        const code = tsSrc(f);
        assert.ok(!code.includes('/api/dispatcher\'') && !code.includes('setInterval'), f);
    }
    assert.ok(tsSrc('features/work/lib/poll.ts').includes('work.set(data)'));
});

test('the sidebar WORK badges are fed from that same payload, not a third fetch', () => {
    const shell = srcFiles('features/shell').map(tsSrc).join('\n');
    assert.ok(shell.includes('badges:'), 'the shell\'s registry was not scanned');
    assert.ok(!shell.includes("api('/api/dispatcher')"));
    assert.ok(!shell.includes('updateWorkBadges'));
    assert.match(tsSrc('features/work/lib/poll.ts'), /renderBadges\(data\)/);
});
