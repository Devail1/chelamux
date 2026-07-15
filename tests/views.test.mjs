// The VIEW REGISTRY — the contract that makes a view cheap to DELETE, not just to
// add. Before it, a view lived in four places (the sidebar markup, selectView's
// timer if/else, main.js's refresh if/else, and the palette's own hardcoded list),
// so a view was never removed and the dashboard carried seven of them.
//
// These tests lock in the two properties that fix depends on:
//   1. add / remove a view by editing the REGISTRY ALONE — the sidebar, the
//      palette and the lifecycle all derive from it (viewreg.js is pure, so this
//      is provable without a DOM);
//   2. ONE poller for /api/dispatcher — Dispatch, Kanban and the sidebar badges
//      each used to fetch it on their own timer. Structural, over the sources.
//
// Run: node --test tests/  (or `uv run pytest -q` — tests/test_js_suites.py runs every .test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { findView, navViews, otherViews, paletteViews, panelId } from '../chela/dashboard/static/js/viewreg.js';

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard', 'static', 'js');
const src = f => readFileSync(join(JS_DIR, f), 'utf8');

// A registry shaped exactly like views.js, with the hooks stubbed out.
function fakeRegistry() {
    return [
        { id: 'feed', label: 'Feed', icon: '≡' },
        { id: 'terminals', label: 'Wall', icon: '▦', enabled: ctx => !!ctx.terminalsOn },
        { id: 'work', label: 'Work', icon: '▤', badges: [{ id: 'side-runs-count' }] },
        { id: 'knowledge', label: 'Knowledge', icon: '◆' },
        { id: 'agents', label: 'Agents', icon: '▢' },
        { id: 'personas', label: 'Personas', icon: '🎭' },
        { id: 'agent-detail', label: 'Agent', virtual: true },
    ];
}

const CTX = { terminalsOn: true };

// The declaration order of the REAL views.js — pulled from source because the
// module can't be imported here (its hooks reach for `window`). This is the one
// property fakeRegistry() cannot vouch for: reorder two entries in views.js and
// every fake-based test below stays green. So we read the real order from disk
// and every order assertion checks against IT, not a hand-copy.
function shippedOrder() {
    const body = src('views.js').split('export const VIEWS')[1];
    return [...body.matchAll(/^\s+id:\s*'([^']+)'/gm)].map(m => m[1]);
}

// --- the registry is the ONE declaration ------------------------------------

// GUARD: the shipped nav order is Feed · Wall · Work · Knowledge · Agents, with
// agent-detail as the trailing virtual drill-in. fakeRegistry() copies views.js
// by hand, so on its own it proves nothing about what actually ships — swap two
// entries in views.js and the fake stays put. This ties the fake to the real
// file: if they diverge (a reorder, an add, a delete in views.js), it goes red.
test('the REAL views.js declares the shipped order — Feed·Wall·Work·Knowledge·Agents·Personas', () => {
    assert.deepEqual(shippedOrder(), ['feed', 'terminals', 'work', 'knowledge', 'agents', 'personas', 'agent-detail']);
    // …and fakeRegistry() is an HONEST copy of it — same ids, same order — so the
    // derivation tests below are exercising the order that actually ships.
    assert.deepEqual(fakeRegistry().map(v => v.id), shippedOrder());
});

test('the sidebar and the palette both derive from the registry — same views, same order', () => {
    const views = fakeRegistry();
    const nav = navViews(views, CTX).map(v => v.id);
    const palette = paletteViews(views, CTX).map(v => v.id);
    assert.deepEqual(nav, ['feed', 'terminals', 'work', 'knowledge', 'agents', 'personas']);
    assert.deepEqual(palette, nav);
});

test('ADDING a view is one registry entry — nav, palette and lookup all pick it up', () => {
    const views = fakeRegistry();
    views.push({ id: 'costs', label: 'Costs', icon: '$' });

    assert.ok(navViews(views, CTX).some(v => v.id === 'costs'));
    assert.ok(paletteViews(views, CTX).some(v => v.id === 'costs'));
    assert.equal(findView(views, 'costs').label, 'Costs');
    assert.equal(panelId('costs'), 'panel-costs');   // the one DOM contract, kept
});

test('REMOVING a view is one registry deletion — it leaves the nav, the palette AND the lifecycle', () => {
    const views = fakeRegistry().filter(v => v.id !== 'knowledge');

    assert.equal(findView(views, 'knowledge'), null);
    assert.ok(!navViews(views, CTX).some(v => v.id === 'knowledge'));
    assert.ok(!paletteViews(views, CTX).some(v => v.id === 'knowledge'));
    // …and nothing else is disturbed: the others still stand.
    assert.deepEqual(navViews(views, CTX).map(v => v.id), ['feed', 'terminals', 'work', 'agents', 'personas']);
});

test('a virtual view (agent-detail) is reachable but is NOT a nav item or a palette entry', () => {
    const views = fakeRegistry();
    assert.ok(findView(views, 'agent-detail'));                              // reachable
    assert.ok(!navViews(views, CTX).some(v => v.id === 'agent-detail'));     // no nav row
    assert.ok(!paletteViews(views, CTX).some(v => v.id === 'agent-detail')); // no palette row
});

test('a disabled view vanishes from the chrome (the Wall, when terminals are off)', () => {
    const views = fakeRegistry();
    const ids = navViews(views, { terminalsOn: false }).map(v => v.id);
    assert.ok(!ids.includes('terminals'));
    assert.deepEqual(ids, ['feed', 'work', 'knowledge', 'agents', 'personas']);
});

test('entering a view tells every OTHER view to let go — no if/else chain to extend', () => {
    const views = fakeRegistry();
    const left = otherViews(views, 'work').map(v => v.id);
    assert.ok(!left.includes('work'));
    assert.equal(left.length, views.length - 1);
});

// --- one dataset, one poller -------------------------------------------------

test('exactly ONE module fetches /api/dispatcher — work.js', () => {
    const owners = readdirSync(JS_DIR)
        .filter(f => f.endsWith('.js'))
        .filter(f => src(f).includes("api('/api/dispatcher')"));
    assert.deepEqual(owners, ['work.js']);
});

test('the Work renderers take a payload — they do not fetch one', () => {
    for (const f of ['kanban.js', 'dispatcher.js']) {
        assert.ok(!src(f).includes('setInterval'), `${f} must not own a poll timer`);
    }
    assert.ok(src('work.js').includes('renderKanban(data)'));
    assert.ok(src('work.js').includes('renderDispatcher(data)'));
});

test('the sidebar WORK badges are fed from that same payload, not a third fetch', () => {
    assert.ok(!src('nav.js').includes("api('/api/dispatcher')"));
    assert.ok(!src('nav.js').includes('updateWorkBadges'));
    assert.ok(src('work.js').includes('workBadgeCounts'));
});

// --- the Feed rides the existing stream --------------------------------------

// ⚠️ This test used to assert the resume CONTRACT by grepping feed.js for the string
// `batch.last_seq` — and it was RED on dev, against correct code: CMX-60's bounded
// catch-up loop legitimately reads `last_seq` to know it has reached the tail. A grep
// tests spelling; it fails the right code and would pass the wrong code under another
// name. The contract now lives in feedmodel.js's `drainLog` (pure) and is proven
// BEHAVIOURALLY in tests/feed.test.mjs — against a fake log, alongside a reader that
// resumes from `last_seq` and is SHOWN to skip 15 of 25 events. What is left here is
// the only thing a source-level test can honestly claim: the Feed has ONE reader.
test('the Feed reads /api/log through the one drain — no second event source', () => {
    const feed = src('feed.js');
    assert.ok(feed.includes("'/api/log?'"));
    assert.ok(feed.includes('drainLog'));           // the cursor rule, tested in feed.test.mjs
    assert.ok(!feed.includes('new EventSource'));   // it rides sse.js's stream, it opens none
    assert.ok(feed.includes('_gap'));               // a gap is rendered, not swallowed
});

test('the log delta rides the ONE EventSource — no second stream is opened', () => {
    const sse = src('sse.js');
    assert.equal((sse.match(/new EventSource/g) || []).length, 1);
    assert.ok(sse.includes("addEventListener('log'"));
    assert.ok(sse.includes('onLogDelta'));          // the frame triggers a fetch
});
