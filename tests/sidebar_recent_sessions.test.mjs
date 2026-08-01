// SIDEBAR "RECENT SESSIONS" — one-click resume, IN A REAL DOM (CMX-208).
//
// A UI over chela/restore.py's already-tested classification: /api/restore returns
// the MANUAL rows (a Claude session a hard tmux death orphaned, with enough on record
// to relaunch), and nav.js renders them into their own sidebar section with a Resume
// button that POSTs /api/restore/resume. This runs the REAL nav.js in a REAL DOM
// (jsdom) against a scriptable fake fetch, the same rig tests/sidebar.test.mjs and
// tests/sidebar_wall_indicator.test.mjs use — so what's asserted is what RENDERS and
// what the button ACTUALLY POSTS, not a source grep.
//
// Run: node --test tests/sidebar_recent_sessions.test.mjs  (pytest runs it via
// tests/test_js_suites.py; needs `npm ci` for jsdom.)
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const BODY = `
<header class="topbar">
  <button class="icon-btn sidebar-toggle" id="btn-menu" aria-expanded="true"
          onclick="chela.toggleSidebar()"></button>
</header>
<div class="app">
  <aside class="sidebar">
    <section class="side-section"><div class="side-list" id="side-nav"></div></section>
    <section class="side-section">
      <span class="side-count" id="hdr-agents">-/-</span>
      <div class="side-list" id="sidebar-agents"><div class="side-empty">No agents</div></div>
    </section>
    <section class="side-section" id="side-recent-section" hidden>
      <span class="side-count" id="hdr-recent">0</span>
      <div class="side-list" id="side-recent"></div>
    </section>
  </aside>
</div>`;

const ROW = {
    store: 'session-ids', wid: '@5', session_id: 'bbbbbbbb-1111-2222-3333-444444444444',
    cwd: '/home/liav/projects/five', label: 'five', stamped_epoch: '786-1784045825',
};

let RECENT = [];               // what GET /api/restore answers with
let RESUME_OK = true;          // what POST /api/restore/resume answers with
let RESUME_CALLS = [];         // every resume request body, in order

function fakeFetch(url, opts) {
    const path = String(url);
    const method = (opts && opts.method) || 'GET';
    if (path.endsWith('/api/agents')) return _json([]);
    if (path.endsWith('/api/agents/context')) return _json({});
    if (path.endsWith('/api/restore/resume') && method === 'POST') {
        RESUME_CALLS.push(JSON.parse(opts.body));
        return _json(RESUME_OK
            ? { ok: true, name: 'shell-9', cwd: ROW.cwd, wid: '@99' }
            : { ok: false, error: 'this row no longer matches — refresh and retry' });
    }
    if (path.endsWith('/api/restore') && method === 'GET') return _json(RECENT);
    return _json({});
}
function _json(body) { return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }); }

let nav, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${BODY}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.fetch = fakeFetch;
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    globalThis.confirm = () => true;
    globalThis.alert = () => {};

    // nav.js/main.js are a cycle — main.js is the real entry point (see sidebar.test.mjs).
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    nav = await import('../chela/dashboard/static/js/nav.js');
});

beforeEach(() => {
    RECENT = [];
    RESUME_OK = true;
    RESUME_CALLS = [];
    document.getElementById('side-recent-section').hidden = true;
    document.getElementById('side-recent').innerHTML = '';
});

// --- render: hidden when empty, visible with a row when not ----------------------

test('the section is hidden when there is nothing to resume', () => {
    nav.renderRecentSessions([]);
    assert.equal(document.getElementById('side-recent-section').hidden, true);
});

test('a resumable row renders its label, cwd and a Resume button', () => {
    nav.renderRecentSessions([ROW]);
    const section = document.getElementById('side-recent-section');
    assert.equal(section.hidden, false, 'a non-empty list must reveal the section');
    const row = document.querySelector('#side-recent .recent-row');
    assert.ok(row, 'no row rendered for a resumable session');
    assert.equal(row.querySelector('.agent-row-name').textContent, ROW.label);
    assert.ok(row.textContent.includes(ROW.cwd), 'the recorded cwd must be shown');
    const btn = row.querySelector('.recent-resume');
    assert.ok(btn, 'no Resume button rendered');
    assert.equal(btn.dataset.wid, ROW.wid);
    assert.equal(btn.dataset.session, ROW.session_id);
});

test('the count badge tracks the list length', () => {
    nav.renderRecentSessions([ROW, { ...ROW, wid: '@6' }]);
    assert.equal(document.getElementById('hdr-recent').textContent, '2');
});

test('a row falls back to cwd when it has no label — never a blank name', () => {
    nav.renderRecentSessions([{ ...ROW, label: '' }]);
    assert.equal(document.querySelector('#side-recent .agent-row-name').textContent, ROW.cwd);
});

test('an untrusted label is ESCAPED, never parsed as markup', () => {
    nav.renderRecentSessions([{ ...ROW, label: '<img src=x>' }]);
    const name = document.querySelector('#side-recent .agent-row-name');
    assert.equal(name.textContent, '<img src=x>');
    assert.equal(name.querySelector('img'), null);
});

// --- refresh: the live fetch, end to end ------------------------------------------

test('refreshRecentSessions pulls /api/restore and paints the result', async () => {
    RECENT = [ROW];
    await nav.refreshRecentSessions();
    assert.equal(document.getElementById('side-recent-section').hidden, false);
    assert.equal(document.querySelectorAll('#side-recent .recent-row').length, 1);
});

// --- resume: the button actually posts, and the row survives its own failure -----

test('clicking Resume POSTs the row identity and re-renders on success', async () => {
    RECENT = [ROW];
    nav.renderRecentSessions([ROW]);
    const btn = document.querySelector('#side-recent .recent-resume');

    RECENT = [];   // the next GET (post-resume refresh) reports nothing left to resume
    await window.chela.resumeSession(btn);

    assert.equal(RESUME_CALLS.length, 1, 'the click must POST exactly one resume request');
    assert.deepEqual(RESUME_CALLS[0], {
        store: ROW.store, wid: ROW.wid, session_id: ROW.session_id,
        stamped_epoch: ROW.stamped_epoch,
    });
    // A successful resume re-fetches the list — the now-resumed row is gone.
    assert.equal(document.getElementById('side-recent-section').hidden, true,
        'the section must hide once the resumed row no longer comes back from the server');
});

test('a failed resume re-enables the button instead of leaving it stuck on "Resuming…"', async () => {
    RESUME_OK = false;
    nav.renderRecentSessions([ROW]);
    const btn = document.querySelector('#side-recent .recent-resume');

    await window.chela.resumeSession(btn);

    assert.equal(btn.disabled, false, 'a declined resume must not leave the button dead');
    assert.equal(btn.textContent, 'Resume', 'the button must recover its original label');
});

test('a double-click cannot fire the request twice — the button disables itself first', async () => {
    nav.renderRecentSessions([ROW]);
    const btn = document.querySelector('#side-recent .recent-resume');

    const first = window.chela.resumeSession(btn);
    const second = window.chela.resumeSession(btn);   // fired before `first` resolves
    await Promise.all([first, second]);

    assert.equal(RESUME_CALLS.length, 1,
        'a click while the button is already disabled must be a no-op, not a second POST');
});
