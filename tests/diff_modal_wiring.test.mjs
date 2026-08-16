// PER-SESSION DIFF MODAL WIRING (CMX-299 rework round 1 + round 2) — a real
// click on a real wall tile's "Files" chip actually opens the diff modal,
// AND the patch drill-down (a real click on a rendered file row) actually
// fills the patch view, AND closing actually closes.
//
// The judge's first round found THREE distinct wiring points nothing
// exercised together, each one that would leave every other test green if
// broken:
//   - _ctxBarHTML (terminals.js) must actually EMIT the `.gs-files` chip —
//     it's the only reachable path into this whole surface.
//   - terminals.js's side-effect `import './diffpanel.js'` is what registers
//     `window.chela.openDiffModal` for the chip's inline onclick — drop the
//     import and the chip is present but dead.
//   - the `#modal-diff` overlay in templates/index.html is the exact node
//     `showModal('modal-diff')` toggles `.active` on; rename it and the
//     click chain runs end to end with nothing ever appearing on screen.
//
// Round 2 found the file-list -> patch half was still unreached: nothing
// ever dispatched a real click on a `.diff-file-row`, so `_diffModalClick`
// being registered on the overlay, `closeDiffModal` actually closing
// `#modal-diff`, `_fileListHtml` passing each row its OWN status (not a
// hardcoded one), and `summaryLabel` actually reaching the rendered header
// were all unguarded — every one of them is provably pure/correct in
// isolation (diffpanel_model.test.mjs) while the wire into the DOM was not.
// This file now drives the SAME real click chain one hop further: chip click
// -> modal open -> file rows render (two, with DIFFERENT statuses) -> a real
// bubbling click on a row -> the patch view fills from a fetched patch ->
// close actually closes.
//
// tests/diffpanel_model.test.mjs only covers the PURE helpers in
// diffpanelmodel.js (no DOM); nothing simulated the boundary a mouse click
// actually crosses. This mirrors tests/kanban_task_modal_wiring.test.mjs
// (CMX-290)'s own closing of the identical shape on the kanban card -> task
// modal chain — same defeat shape, different surface (see
// docs/defeat_shapes/ for the general form: a click chain with a wiring
// guard on one link and none on the others).
//
// Along the way this also proves the chip is not just present but VISIBLE:
// asserting the rendered `git-compare` icon actually carries SVG content
// (not an empty `<svg></svg>`) closes the icon-glyph mutation the judge also
// found (util.js's `_LUCIDE['git-compare']` emptied out) — an icon-only
// button with no glyph is indistinguishable from a working one by every
// other assertion here.
//
// Run: node --test tests/diff_modal_wiring.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it
import { clickOnclick, sliceTemplate } from './js_helpers/dashboard_dom.mjs';

// Sliced straight out of the REAL templates/index.html — not a hand-typed
// copy — so a rename of `#modal-diff` (or the loss of `.modal-overlay`'s
// visibility-gating class) shows up here, not just in a fixture that happens
// to still agree with today's markup.
const MODAL_DIFF_HTML = sliceTemplate(
    '<div class="modal-overlay" id="modal-diff">', '<!-- /modal-diff -->');

const PANEL = `
<div class="panel" id="panel-terminals">
  <button id="term-mode-single"></button>
  <button id="term-mode-wall"></button>
  <select id="term-agent"></select>
  <span id="term-wall-grid"><span id="term-grid-presets"></span><button id="term-lock-btn"></button></span>
  <button id="term-new-shell"></button>
  <div id="term-switcher"></div>
  <div id="term-stage"></div>
  <div id="term-min-dock"></div>
  <div id="term-bar" class="kb-collapsed"><button class="kb-toggle" id="kb-toggle"></button><div class="kb-body" id="kb-body"></div></div>
</div>
${MODAL_DIFF_HTML}`;

const AGENTS = [{ name: 'w1', window_id: '@1', online: true }];

// Two files with DIFFERENT statuses — a hardcoded statusMeta('modified')
// call site would make both rows carry the same chip class/label, which a
// single-file fixture can never catch (its one row's real status happens to
// be 'modified' too).
const DIFF_STATE = {
    is_git: true, has_head: true,
    files: [
        { path: 'a.py', status: 'modified', additions: 3, deletions: 1 },
        { path: 'b.py', status: 'added', additions: 5, deletions: 0 },
    ],
    additions: 8, deletions: 1,
};

const PATCH_TEXT = [
    'diff --git a/a.py b/a.py',
    'index abc1234..def5678 100644',
    '--- a/a.py',
    '+++ b/a.py',
    '@@ -1,2 +1,2 @@',
    ' context line',
    '-old line',
    '+new line',
].join('\n') + '\n';

function fakeFetch(url) {
    const path = String(url);
    let body = {};
    if (path.endsWith('/api/agents')) body = AGENTS;
    else if (path.endsWith('/api/agents/context')) body = [];
    else if (path.endsWith('/api/rooms')) body = { rooms: {}, pending: [] };
    else if (path.startsWith('/api/term/ready')) body = { ready: true };
    else if (path.startsWith('/api/agents/%401/diff/patch')) body = { ok: true, patch: PATCH_TEXT };
    else if (path.endsWith('/api/agents/%401/diff')) body = DIFF_STATE;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

function fakeGridStack() {
    const grid = {
        on() {}, off() {}, save: () => [], destroy() {}, removeWidget(el) { el.remove(); },
        addWidget: el => el, makeWidget: el => el, enableMove() {}, enableResize() {},
        update() {}, batchUpdate() {}, commit() {}, cellHeight() {}, column() {},
        getGridItems: () => [], removeAll() {}, float() {}, engine: { nodes: [] },
    };
    return { init: () => grid };
}

let terminals, util;

before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body>${PANEL}</body></html>`,
        { url: 'http://localhost:5005/', pretendToBeVisual: true });
    dom.window.TERMINALS_ENABLED = true;
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement',
        'Element', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent',
        'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
        // defineProperty, NOT assignment: from node 21 `globalThis.navigator` has only a
        // getter, so plain assignment THROWS. (Same rig as tests/wallnav.test.mjs.)
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.GridStack = fakeGridStack();
    globalThis.fetch = fakeFetch;
    dom.window.document.elementFromPoint = () => null;
    dom.window.matchMedia = q => ({
        media: q, matches: false, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {},
    });
    globalThis.window.chela = globalThis.window.chela || {};
    globalThis.setInterval = () => 0;
    // Browser-faithful import order (main.js is the entry; nav <-> main is a cycle).
    await import('../chela/dashboard/static/js/main.js');
    util = await import('../chela/dashboard/static/js/util.js');
    terminals = await import('../chela/dashboard/static/js/terminals.js');
    util.setCurrentTab('terminals');
    util.setAgentsCache(AGENTS);
    await terminals.renderTerminals();
});

const flush = () => new Promise(r => setTimeout(r, 0));

// jsdom never executes inline `onclick=` attributes on a dispatched click
// event without `runScripts:"dangerously"` (not set here) — same reasoning
// as tests/js_helpers/dashboard_dom.mjs's clickOnclick(), extended with an
// `event` argument since this chip's onclick calls `event.stopPropagation()`
// before the handler (clickOnclick's own Function ctor only binds `chela`).
function clickFilesChip(el) {
    if (!el) throw new Error('clickFilesChip: element is missing');
    const onclick = el.getAttribute('onclick');
    if (!onclick) throw new Error('clickFilesChip: element has no onclick attribute');
    return new Function('chela', 'event', onclick).call(el, globalThis.window.chela, { stopPropagation() {} });
}

test('a real click on the wall tile\'s "Files" chip opens the REAL #modal-diff, visibly, and fills it from the session\'s live diff', async () => {
    const modal = document.getElementById('modal-diff');
    assert.ok(modal, 'sliceTemplate did not carry #modal-diff into the fixture');
    assert.equal(modal.classList.contains('active'), false,
        'the diff modal starts open — the assertions below could not tell a real open from a no-op');

    // 🔴 GUARD (WIRING #1): _ctxBarHTML must actually emit the chip — this is
    // the only reachable path into the whole diff surface.
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    assert.ok(filesBtn, 'the wall tile\'s bottom bar has no .gs-files "Files" chip — _ctxBarHTML dropped it');
    assert.match(filesBtn.getAttribute('onclick') || '', /chela\.openDiffModal\('@1'\)/,
        'the Files chip is not wired to chela.openDiffModal');

    // 🔴 GUARD (MUTATION #3): the chip is ICON-ONLY — its `title` attribute is
    // its entire accessible name AND its only tooltip. An emptied title would
    // leave the button with no visible-affordance regression the SVG-glyph
    // check below can't catch (the icon can be present while the title is
    // blank).
    assert.equal(filesBtn.getAttribute('title'), 'Changed files',
        'the Files chip lost its "Changed files" title — its only accessible name/tooltip');

    // 🔴 GUARD (MUTATION #4): the chip is icon-only — an emptied `git-compare`
    // glyph leaves a button with no visible affordance at all, invisible to
    // every assertion above (the onclick attribute is untouched either way).
    assert.ok(/<circle|<path/.test(filesBtn.innerHTML),
        'the Files chip rendered no SVG glyph content — the git-compare icon is empty');

    // 🔴 GUARD (WIRING #2): if diffpanel.js's side-effect import were dropped,
    // window.chela.openDiffModal would not exist and this throws.
    clickFilesChip(filesBtn);

    // 🔴 GUARD (WIRING #3): showModal('modal-diff') must find THIS exact node
    // — a renamed id leaves the click chain running end to end with nothing
    // ever appearing on screen (showModal degrades silently: no element, no
    // throw, just no `.active`).
    assert.equal(modal.classList.contains('active'), true,
        'clicking the Files chip never made #modal-diff visible — openDiffModal -> showModal chain is broken');

    await flush();   // let openDiffModal's /diff fetch + _render resolve

    const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
    assert.equal(rows.length, 2, 'the diff modal did not render the fetched file list');
    assert.equal(rows[0].dataset.diffFile, 'a.py', 'the rendered file row does not match the fetched diff state');
    assert.equal(rows[1].dataset.diffFile, 'b.py', 'the rendered file row does not match the fetched diff state');

    // 🔴 GUARD (MUTATION #4): each row must carry ITS OWN status, not a
    // hardcoded one — a fixture with only one file (always 'modified') could
    // never distinguish `statusMeta(f.status)` from `statusMeta('modified')`.
    const chip = row => row.querySelector('.diff-status-chip');
    assert.ok(chip(rows[0]).classList.contains('diff-status-modified'),
        "a.py's chip did not carry its own ('modified') status");
    assert.ok(chip(rows[1]).classList.contains('diff-status-added'),
        "b.py's chip did not carry its own ('added') status — statusMeta may be hardcoded");
    assert.notEqual(chip(rows[0]).textContent, chip(rows[1]).textContent,
        'both rows rendered the same status label for two files with different statuses');

    // 🔴 GUARD (round-3 judge finding #6): each row's own +A/−D pair must
    // reflect THAT file's own additions/deletions — the modal-level summary
    // below is a SUM across files, so it stays identical whether a per-row
    // stat swaps additions for deletions; only reading each row's own stat
    // text can tell `−${f.deletions}` from `−${f.additions}` apart.
    const stat = row => row.querySelector('.diff-file-stat').textContent;
    assert.equal(stat(rows[0]), '+3 −1', "a.py's own +A/−D stat does not match its own additions/deletions");
    assert.equal(stat(rows[1]), '+5 −0', "b.py's own +A/−D stat does not match its own additions/deletions");

    // 🔴 GUARD (MUTATION #5): summaryLabel's one-glance header must actually
    // reach the modal, not just exist as a passing pure-function test.
    const summary = document.querySelector('.diff-modal-summary');
    assert.ok(summary, 'no .diff-modal-summary element was rendered');
    assert.equal(summary.textContent, '2 files changed · +8 −1',
        'the rendered summary does not match summaryLabel(state) for this diff state');

    // 🔴 GUARD (WIRING #1): a real, bubbling click on a rendered file row must
    // reach `_diffModalClick`, which is registered on the #modal-diff overlay
    // by `_bindDiffModalDismiss` — calling the row's onclick directly (as
    // clickFilesChip does for the chip) would not exercise that registration
    // at all, since file rows have no onclick attribute of their own.
    const patchView = document.getElementById('diff-patch-view');
    assert.ok(patchView, 'no #diff-patch-view element was rendered');
    rows[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await flush();   // let _loadDiffPatch's /diff/patch fetch + _patchHtml resolve

    assert.ok(rows[0].classList.contains('active'),
        'clicking a file row did not mark it active — _diffModalClick never ran (overlay listener missing?)');
    assert.equal(patchView.querySelectorAll('.diff-line-meta').length, 2,
        'the patch view is missing the +++/--- header lines');
    assert.equal(patchView.querySelectorAll('.diff-line-hunk').length, 1,
        'the patch view is missing the @@ hunk line');
    assert.equal(patchView.querySelectorAll('.diff-line-add').length, 1,
        'the patch view is missing the added line');
    assert.equal(patchView.querySelectorAll('.diff-line-del').length, 1,
        'the patch view is missing the removed line');

    // 🔴 GUARD (MUTATION #5): the file list is single-select — opening a
    // SECOND file must clear the first row's `.active` highlight. Only
    // clicking one row (as above) can never exercise the deselect sweep
    // (`$$('.diff-file-row.active').forEach(...remove('active'))`); a
    // narrowed selector (e.g. `.active-never`) that never matches anything
    // would leave both rows reading `.active` at once and stay invisible
    // unless a SECOND row is actually clicked.
    rows[1].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await flush();
    assert.equal(rows[0].classList.contains('active'), false,
        'clicking a second file row did not clear the first row\'s .active highlight — the deselect sweep is broken');
    assert.ok(rows[1].classList.contains('active'),
        'clicking the second file row did not mark it active');

    // 🔴 GUARD (WIRING #2): closeDiffModal must close THIS #modal-diff node —
    // routing `closeModal` at the wrong id would leave the overlay `.active`
    // forever, with every assertion above still green.
    window.chela.closeDiffModal();
    assert.equal(modal.classList.contains('active'), false,
        'chela.closeDiffModal() did not remove #modal-diff\'s .active class');
});

// ---------------------------------------------------------------------------
// Round 3 (2026-08-16, PR #373 judge) — the test above proves closeDiffModal()
// itself works by calling it DIRECTLY, which exercises none of the THREE
// routes index.html's own comment claims lead into it (close button, Escape,
// backdrop click) — see docs/defeat_shapes/64 (a comment enumerates N entry
// paths into one shared action; driving the action directly proves none of
// them). Each of these dispatches a REAL DOM event down its own named route.
// ---------------------------------------------------------------------------

test('a real Escape keydown (not a direct closeDiffModal() call) closes the diff modal', async () => {
    const modal = document.getElementById('modal-diff');
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    clickFilesChip(filesBtn);
    await flush();
    assert.equal(modal.classList.contains('active'), true, 'setup: the diff modal did not open');

    // 🔴 GUARD: dead-coding _diffModalKey's `e.key === 'Escape'` check
    // (`if (false && e.key === 'Escape') closeDiffModal();`) leaves a real
    // Escape keypress silently doing nothing while this stays green if the
    // suite never dispatches a real keydown.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal(modal.classList.contains('active'), false,
        'a real Escape keydown did not close #modal-diff — _diffModalKey is not wired (or dead-coded)');
});

test('a real click on the #modal-diff backdrop (the overlay itself, not the dialog) closes the diff modal', async () => {
    const modal = document.getElementById('modal-diff');
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    clickFilesChip(filesBtn);
    await flush();
    assert.equal(modal.classList.contains('active'), true, 'setup: the diff modal did not open');

    // 🔴 GUARD: dead-coding _diffModalBackdrop's `e.target.id === 'modal-diff'`
    // check leaves a real backdrop click silently doing nothing. Dispatching
    // directly on `modal` (not a descendant) makes `e.target === modal`, the
    // exact condition the backdrop check tests — a click on the dialog body
    // itself must NOT close it, which is exactly why this checks `.id`.
    modal.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    assert.equal(modal.classList.contains('active'), false,
        'a real click on the #modal-diff backdrop did not close it — _diffModalBackdrop is not wired (or dead-coded)');
});

test("openDiffModal's stale-flight guard: a /diff response landing AFTER the modal was closed must not render into it", async () => {
    const modal = document.getElementById('modal-diff');
    const content = document.getElementById('diff-modal-content');
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');

    let resolveDiff;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.endsWith('/api/agents/%401/diff')) {
            return new Promise(resolve => {
                resolveDiff = () => resolve({ ok: true, status: 200, json: () => Promise.resolve(DIFF_STATE) });
            });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn);
        await flush();   // openDiffModal is now awaiting the deferred /diff fetch
        assert.equal(modal.classList.contains('active'), true, 'setup: the diff modal did not open');
        const contentBeforeLateResponse = content.innerHTML;

        window.chela.closeDiffModal();
        assert.equal(modal.classList.contains('active'), false, 'setup: the diff modal did not close');

        // 🔴 GUARD: dead-coding `if (wid !== _openWid) return;` in openDiffModal
        // lets this late response re-render #diff-modal-content anyway — the
        // synchronous fakeFetch used everywhere else in this file resolves
        // before the modal could ever be closed first, so only a DEFERRED
        // response can reproduce the ordering this guard exists for.
        resolveDiff();
        await flush();
        assert.equal(content.innerHTML, contentBeforeLateResponse,
            'a /diff response that landed AFTER the modal closed still re-rendered #diff-modal-content — the stale-flight guard did not fire');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("_loadDiffPatch's stale-flight guard: a /diff/patch response landing AFTER the modal was closed must not overwrite the patch view", async () => {
    const modal = document.getElementById('modal-diff');
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    clickFilesChip(filesBtn);
    await flush();
    const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
    const patchView = document.getElementById('diff-patch-view');
    assert.equal(rows.length, 2, 'setup: the diff modal did not render the fetched file list');

    let resolvePatch;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            return new Promise(resolve => {
                resolvePatch = () => resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, patch: PATCH_TEXT }) });
            });
        }
        return fakeFetch(url);
    };
    try {
        rows[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();   // _loadDiffPatch is now awaiting the deferred /diff/patch fetch
        const viewBeforeLateResponse = patchView.innerHTML;

        window.chela.closeDiffModal();

        // 🔴 GUARD: dead-coding `if (wid !== _openWid) return;` in
        // _loadDiffPatch lets this late response overwrite #diff-patch-view
        // even though the modal moved on — same ordering issue as the
        // openDiffModal guard above, on the patch-drilldown half.
        resolvePatch();
        await flush();
        assert.equal(patchView.innerHTML, viewBeforeLateResponse,
            'a /diff/patch response that landed AFTER the modal closed still overwrote #diff-patch-view — the stale-flight guard did not fire');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('a changed file path containing HTML metacharacters is escaped, not spliced raw, into the rendered file row', async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    // The trailing `"` is the metacharacter that matters for the
    // data-diff-file ATTRIBUTE half of this test (below): escHtml alone
    // does NOT escape quote characters (see util.js's own comment on it),
    // so a path with no `"` in it can pass whether the row uses attrEsc or
    // escHtml for that attribute — only a quote-bearing path can tell them
    // apart. A path containing `"` is legal on Linux and can come straight
    // out of `git ls-files --others` in an agent's worktree.
    const evilPath = '<img src=x onerror=alert(1)>".js';
    const evilState = {
        is_git: true, has_head: true,
        files: [{ path: evilPath, status: 'modified', additions: 1, deletions: 0 }],
        additions: 1, deletions: 0,
    };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.endsWith('/api/agents/%401/diff')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(evilState) });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn);
        await flush();
        const pathEl = document.querySelector('#diff-modal-content .diff-file-path');
        assert.ok(pathEl, 'no .diff-file-path element was rendered');
        // 🔴 GUARD: `${f.path}` instead of `${escHtml(f.path)}` splices the
        // path straight into the row's innerHTML — a path containing `<img>`
        // then parses as a REAL element instead of text, which flips
        // querySelector('img') from null to a match and truncates the
        // visible text down to whatever trails the tag.
        assert.equal(pathEl.querySelector('img'), null,
            'the file path was parsed as real HTML — a <img> tag inside it was not escaped');
        assert.equal(pathEl.textContent, evilPath,
            'the rendered path text does not match the fetched path — escHtml is missing or mangled it');

        // 🔴 GUARD (MUTATION #2): the SAME path is also spliced into the row's
        // data-diff-file ATTRIBUTE (the value the click handler reads back and
        // sends to /diff/patch) — that needs attrEsc, not escHtml, since
        // escHtml leaves `"` unescaped. Using escHtml there breaks the
        // attribute open at the `"` in evilPath, so the parsed-back
        // dataset.diffFile comes back truncated instead of matching evilPath.
        const row = document.querySelector('#diff-modal-content .diff-file-row');
        assert.ok(row, 'no .diff-file-row element was rendered');
        assert.equal(row.dataset.diffFile, evilPath,
            'the row\'s data-diff-file attribute does not match the fetched path — it needs attrEsc (quote-safe), not escHtml, for an attribute value');
        window.chela.closeDiffModal();
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('the Files chip escapes a wid containing a single quote so its onclick argument cannot break out of the inline JS string literal', async () => {
    const evilWid = "@2'x";
    try {
        util.setAgentsCache([{ name: 'w2', window_id: evilWid, online: true }]);
        await terminals.renderTerminals();
        const filesBtn2 = document.querySelector('.gs-files');
        assert.ok(filesBtn2, 'no .gs-files chip rendered for the quote-bearing wid');
        const onclick = filesBtn2.getAttribute('onclick');
        assert.ok(onclick, 'the chip has no onclick attribute');

        let received;
        const spyChela = { openDiffModal(wid) { received = wid; }, closeDiffModal() {} };
        // 🔴 GUARD (MUTATION #5): `String(wid)` instead of
        // `escHtml(wid).replace(/'/g, "\\'")` leaves the raw `'` in place —
        // `chela.openDiffModal('@2'x')` is then a SYNTAX ERROR (the string
        // literal ends at the second `'`, leaving a bare `x` token), which
        // `new Function` throws on at compile time, before the handler could
        // ever run for real.
        assert.doesNotThrow(() => {
            new Function('chela', 'event', onclick).call(filesBtn2, spyChela, { stopPropagation() {} });
        }, 'the onclick attribute is not valid JS — an unescaped quote in the wid broke out of the inline string literal');
        assert.equal(received, evilWid,
            "openDiffModal was not called with the wid's full, unescaped-back value");
    } finally {
        // restore the single-agent fixture every other test in this file depends on
        util.setAgentsCache(AGENTS);
        await terminals.renderTerminals();
    }
});

// ---------------------------------------------------------------------------
// Round 4 (2026-08-16, PR #373 judge round 3) — two more surviving mutations:
// the close button's own onclick attribute (the third dismissal route
// index.html's own comment enumerates, after Escape and the backdrop — both
// already covered above) was still never invoked, and _loadDiffPatch's error
// arm (the server's own {'ok': false, 'error': ...} reason) was never armed
// by any fixture, since every fetch stub in this file returns ok:true.
// ---------------------------------------------------------------------------

test('a real click on the #modal-diff close button (not a direct closeDiffModal() call) closes the diff modal', async () => {
    const modal = document.getElementById('modal-diff');
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    clickFilesChip(filesBtn);
    await flush();
    assert.equal(modal.classList.contains('active'), true, 'setup: the diff modal did not open');

    // 🔴 GUARD: the close button's own onclick attribute is the THIRD
    // dismissal route (after Escape and the backdrop, both proven above by
    // a real dispatched event) — a dead-coded or detached onclick leaves it
    // silently doing nothing while every other test in this file still
    // passes.
    const closeBtn = modal.querySelector('.task-modal-close');
    assert.ok(closeBtn, 'the diff modal has no .task-modal-close button');
    assert.match(closeBtn.getAttribute('onclick') || '', /chela\.closeDiffModal\(\)/,
        'the close button is not wired to chela.closeDiffModal()');
    clickOnclick(closeBtn);
    assert.equal(modal.classList.contains('active'), false,
        'a real click on the #modal-diff close button did not close it — its onclick is not wired (or dead-coded)');
});

test("_loadDiffPatch's error arm shows the server's own reason, not the empty-patch message", async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    const SERVER_ERROR = 'not a changed file in this session';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: false, error: SERVER_ERROR }) });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn);
        await flush();
        const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
        assert.equal(rows.length, 2, 'setup: the diff modal did not render the fetched file list');
        const patchView = document.getElementById('diff-patch-view');

        rows[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();

        // 🔴 GUARD: `if (false && (!res || res.ok === false))` dead-codes this
        // whole branch — every OTHER fixture in this file returns ok:true, so
        // nothing else can arm it. Dead-coded, an {ok: false, error: ...}
        // response falls through to the success renderer and shows the
        // generic empty-patch message instead of the server's real reason.
        assert.match(patchView.textContent, new RegExp(SERVER_ERROR.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
            "the patch view did not show the server's own error reason — _loadDiffPatch's error arm is not wired (or dead-coded)");
        assert.doesNotMatch(patchView.textContent, /No diff text for this file\./,
            'the patch view fell through to the empty-patch message instead of showing the server\'s error');
        window.chela.closeDiffModal();
    } finally {
        globalThis.fetch = originalFetch;
    }
});
