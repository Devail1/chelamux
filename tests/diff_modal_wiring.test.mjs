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
    // 🔴 GUARD: distinctness alone can't tell the designed A/M/D/U/! glyph
    // from any other pair of distinct strings (e.g. each chip echoing its own
    // CSS class name as its body text) — pin the actual rendered glyphs.
    assert.equal(chip(rows[0]).textContent, 'M', "a.py's chip did not render the 'M' glyph for status modified");
    assert.equal(chip(rows[1]).textContent, 'A', "b.py's chip did not render the 'A' glyph for status added");

    // 🔴 GUARD (MUTATION #3): the chip is a SINGLE LETTER (A/M/D/U/!) — its
    // `title` is the entire expansion of that glyph and its only accessible
    // name. Blanking it leaves a chip whose class/label assertions above stay
    // green while the tooltip (and screen-reader name) silently disappears.
    assert.equal(chip(rows[0]).getAttribute('title'), 'modified',
        "a.py's status chip lost its title — its only accessible name for the glyph");
    assert.equal(chip(rows[1]).getAttribute('title'), 'added',
        "b.py's status chip lost its title — its only accessible name for the glyph");

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
    // 🔴 GUARD: the patch pane's opening affordance — before any file row is
    // clicked, the right-hand pane must say what it's for, not sit empty
    // (indistinguishable from a render that failed).
    assert.equal(patchView.textContent.trim(), 'Select a file to view its diff.',
        'the diff modal opened with an empty patch pane instead of its "Select a file" affordance');
    // 🔴 GUARD (MUTATION #2): dispatched on a CHILD span, not the <li> itself —
    // the row is display:flex and every visible pixel a real click could land
    // on belongs to a child (.diff-status-chip / .diff-file-path /
    // .diff-file-stat). `e.target.matches('.diff-file-row')` only matches when
    // e.target IS the row; `e.target.closest('.diff-file-row')` is required to
    // resolve a click on any of its children, which is the only kind of click
    // a real user can produce.
    rows[0].querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
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
    // 🔴 GUARD (MUTATION #2): again on a child span, not the <li> — see above.
    rows[1].querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
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
// backdrop click) — see docs/defeat_shapes/72 (a comment enumerates N entry
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
    //
    // `#`, `&`, and `+` are added for the click-through half below (round 6,
    // 2026-08-16, PR #373 judge round 5, experiment 4): the clicked path is
    // carried to /diff/patch in a QUERY STRING, and all three are legal
    // filename characters that are NOT metacharacters in HTML but ARE in a
    // query string — `#` truncates the URL at the fragment, `&` splits off a
    // second param, `+` decodes server-side as a space.
    const evilPath = '<img src=x onerror=alert(1)>"#&+.js';
    const evilState = {
        is_git: true, has_head: true,
        files: [{ path: evilPath, status: 'modified', additions: 1, deletions: 0 }],
        additions: 1, deletions: 0,
    };
    const originalFetch = globalThis.fetch;
    let patchUrl = null;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.endsWith('/api/agents/%401/diff')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(evilState) });
        }
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            patchUrl = path;
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

        // 🔴 GUARD (WIRING, round 6): clicking the row must send the path to
        // /diff/patch as a PROPERLY PERCENT-ENCODED query value. Every fixture
        // in this file up to now matched the request by PREFIX
        // (`startsWith('/api/agents/%401/diff/patch')`) and ignored the query
        // entirely, so an unencoded `?path=${path}` would still return the
        // right fixture data without the request URL itself ever being wrong
        // by any test's own admission — asserting the captured URL is the
        // only way to see that.
        row.querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();
        assert.equal(patchUrl, `/api/agents/%401/diff/patch?path=${encodeURIComponent(evilPath)}`,
            'the /diff/patch request URL does not carry the exact percent-encoded path — the query string is not properly encoded');

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

test('the Files chip escapes a wid containing an ampersand so the onclick attribute round-trips it intact', async () => {
    // 🔴 GUARD: the single-quote test above (MUTATION #5) only exercises the
    // `.replace(/'/g, ...)` half of the chip's escaping — its evilWid ("@2'x")
    // carries no character escHtml itself is responsible for, so a mutation
    // that drops `escHtml(wid)` down to `String(wid)` (keeping the quote
    // replace) leaves that test green. escHtml encodes a raw "&" to "&amp;";
    // that's what stops the wid's OWN text from being misread as a second,
    // unintended HTML entity once it's parsed back out of the onclick
    // attribute's markup. Pick a wid whose raw text already contains the
    // literal characters "&amp;" — with escHtml applied first, the raw "&"
    // gets encoded, so parsing the markup back decodes exactly one entity and
    // reproduces the original wid. Drop escHtml (`String(wid)`) and the wid's
    // own "&amp;" text IS a well-formed entity in the emitted markup, so the
    // HTML parser decodes it too — the wid comes back corrupted to "@3&".
    const evilWid = '@3&amp;';
    try {
        util.setAgentsCache([{ name: 'w3', window_id: evilWid, online: true }]);
        await terminals.renderTerminals();
        const filesBtn3 = document.querySelector('.gs-files');
        assert.ok(filesBtn3, 'no .gs-files chip rendered for the ampersand-bearing wid');
        const onclick = filesBtn3.getAttribute('onclick');
        assert.ok(onclick, 'the chip has no onclick attribute');

        let received;
        const spyChela = { openDiffModal(wid) { received = wid; }, closeDiffModal() {} };
        new Function('chela', 'event', onclick).call(filesBtn3, spyChela, { stopPropagation() {} });
        assert.equal(received, evilWid,
            "openDiffModal was not called with the wid's full, unescaped-back value — the wid's own \"&amp;\" text was misread as an HTML entity");
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
    // 🔴 GUARD (round 7, PR #373 judge round 6 finding #5): the button renders
    // as a bare `&times;` glyph, so `aria-label` is its ENTIRE accessible
    // name — nothing else here (class, onclick) proves a screen reader
    // announces anything at all.
    assert.equal(closeBtn.getAttribute('aria-label'), 'Close',
        'the close button lost its "Close" aria-label — its only accessible name for the bare glyph');
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

// ---------------------------------------------------------------------------
// Round 5 (2026-08-16, PR #373 judge round 4) — three more surviving
// mutations: the patch view's OWN escape call (distinct from the file-list
// escape closed above — same helper, different render site, and every
// PATCH_TEXT fixture in this file is pure ASCII so the two are
// indistinguishable there, see docs/defeat_shapes/29); and openDiffModal /
// _loadDiffPatch each dead-coding the "clear the shared DOM target before the
// new fetch resolves" reset, which the round-3/4 stale-flight tests above
// can't catch because they only compare innerHTML BEFORE vs AFTER the late
// response, never what that content actually IS (see docs/defeat_shapes/74).
// ---------------------------------------------------------------------------

test('a patch line containing HTML metacharacters is escaped, not spliced raw, into the rendered patch view', async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    // Every other PATCH_TEXT fixture in this file is pure ASCII with no HTML
    // metacharacter, so `${escHtml(line)}` and `${line}` render byte-identical
    // output for it — this line is chosen specifically to diverge under the
    // two.
    const evilPatch = [
        'diff --git a/a.py b/a.py',
        'index abc1234..def5678 100644',
        '--- a/a.py',
        '+++ b/a.py',
        '@@ -1,2 +1,2 @@',
        ' context line',
        '+<img src=x onerror=alert(1)>',
    ].join('\n') + '\n';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, patch: evilPatch }) });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn);
        await flush();
        const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
        assert.equal(rows.length, 2, 'setup: the diff modal did not render the fetched file list');
        const patchView = document.getElementById('diff-patch-view');

        rows[0].querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();

        // 🔴 GUARD (MUTATION #1): `${line}` instead of `${escHtml(line)}` in
        // _patchHtml splices the raw patch line straight into the view's
        // innerHTML — the `<img>` then parses as a REAL element instead of
        // text.
        assert.equal(patchView.querySelector('img'), null,
            'a patch line was parsed as real HTML — an <img> tag inside it was not escaped');
        const addLine = patchView.querySelector('.diff-line-add');
        assert.ok(addLine, 'the patch view is missing the added line');
        assert.equal(addLine.textContent, '+<img src=x onerror=alert(1)>',
            'the rendered patch line text does not match the fetched line — escHtml is missing or mangled it on the patch half');
        window.chela.closeDiffModal();
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('opening the diff modal for a DIFFERENT session clears the previous session\'s stale file list before the new /diff fetch resolves', async () => {
    const modal = document.getElementById('modal-diff');
    const content = document.getElementById('diff-modal-content');
    const filesBtn1 = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');

    clickFilesChip(filesBtn1);
    await flush();
    assert.match(content.innerHTML, /a\.py/, "setup: session @1's file list did not render");

    window.chela.closeDiffModal();
    assert.equal(modal.classList.contains('active'), false, 'setup: the diff modal did not close');

    util.setAgentsCache([...AGENTS, { name: 'w2', window_id: '@2', online: true }]);
    await terminals.renderTerminals();
    const filesBtn2 = document.querySelector('.term-ctx-bar[data-ctx-for="@2"] .gs-files');
    assert.ok(filesBtn2, 'setup: no .gs-files chip rendered for session @2');

    let resolveDiff2;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.endsWith('/api/agents/%402/diff')) {
            return new Promise(resolve => {
                resolveDiff2 = () => resolve({
                    ok: true, status: 200,
                    json: () => Promise.resolve({
                        is_git: true, has_head: true,
                        files: [{ path: 'c.py', status: 'added', additions: 1, deletions: 0 }],
                        additions: 1, deletions: 0,
                    }),
                });
            });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn2);
        // 🔴 GUARD (MUTATION #4): `if (false && content) content.innerHTML =
        // ...` dead-codes openDiffModal's reset — @2's /diff fetch is still IN
        // FLIGHT here (deferred, not yet resolved), so the only way #diff-
        // modal-content could already be clear of @1's file list is if the
        // reset ran synchronously before the fetch was even issued.
        assert.doesNotMatch(content.innerHTML, /a\.py/,
            "session @1's stale file list is still showing while @2's /diff fetch is in flight — openDiffModal did not clear #diff-modal-content before fetching");
        // 🔴 GUARD (round 7, PR #373 judge round 6 finding #3): the reset write
        // must land the "Loading…" affordance, not just clear the old content
        // — an emptied write (`content.innerHTML = ''`) passes the
        // doesNotMatch check above exactly as well as the real string, but
        // leaves the modal a blank white box for the whole /diff round trip.
        // Proven by PRESENCE, not just absence (see docs/defeat_shapes/74).
        assert.match(content.innerHTML, /Loading…/,
            "@2's /diff fetch is in flight but #diff-modal-content shows no Loading… affordance — openDiffModal's pre-fetch write was emptied, not set");

        resolveDiff2();
        await flush();
        assert.match(content.innerHTML, /c\.py/, "session @2's own file list never rendered");
        assert.doesNotMatch(content.innerHTML, /a\.py/, "session @1's stale file list survived into @2's rendered content");
    } finally {
        globalThis.fetch = originalFetch;
        window.chela.closeDiffModal();
        util.setAgentsCache(AGENTS);
        await terminals.renderTerminals();
    }
});

test('clicking a DIFFERENT file row clears the previous file\'s stale patch text before the new /diff/patch fetch resolves', async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    clickFilesChip(filesBtn);
    await flush();
    const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
    assert.equal(rows.length, 2, 'setup: the diff modal did not render the fetched file list');
    const patchView = document.getElementById('diff-patch-view');

    rows[0].querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await flush();
    assert.match(patchView.textContent, /old line/, "setup: a.py's patch did not render");

    let resolvePatch2;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            return new Promise(resolve => {
                resolvePatch2 = () => resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, patch: 'diff --git a/b.py b/b.py\n+brand new line\n' }) });
            });
        }
        return fakeFetch(url);
    };
    try {
        rows[1].querySelector('.diff-file-path').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        // 🔴 GUARD (MUTATION #5): `if (false) view.innerHTML = ...` dead-codes
        // _loadDiffPatch's reset — b.py's /diff/patch fetch is still IN FLIGHT
        // here (deferred, not yet resolved), so the only way #diff-patch-view
        // could already be clear of a.py's patch is if the reset ran
        // synchronously before the fetch was even issued.
        assert.doesNotMatch(patchView.textContent, /old line/,
            "a.py's stale patch text is still showing while b.py's /diff/patch fetch is in flight — _loadDiffPatch did not clear #diff-patch-view before fetching");
        // 🔴 GUARD (round 7, PR #373 judge round 6 finding #4): the reset write
        // must land the "Loading…" affordance, not just clear the old text —
        // an emptied write (`view.innerHTML = ''`) passes the doesNotMatch
        // check above exactly as well as the real string, but leaves the
        // patch pane blank — indistinguishable from "this file has no diff"
        // — for the whole /diff/patch round trip. Proven by PRESENCE, not
        // just absence (see docs/defeat_shapes/74).
        assert.match(patchView.textContent, /Loading…/,
            "b.py's /diff/patch fetch is in flight but #diff-patch-view shows no Loading… affordance — _loadDiffPatch's pre-fetch write was emptied, not set");

        resolvePatch2();
        await flush();
        assert.match(patchView.textContent, /brand new line/, "b.py's own patch never rendered");
    } finally {
        globalThis.fetch = originalFetch;
        window.chela.closeDiffModal();
    }
});

// ---------------------------------------------------------------------------
// Round 7 (2026-08-17, PR #373 judge round 6) — _fileListHtml's empty state
// ('Nothing to show.') is what a session with a CLEAN worktree sees — the
// single most common state any session is in — but DIFF_STATE above always
// carries files, so no fixture in this file ever rendered it. Dead-coding
// the branch (`if (false && !files.length) return '...';`) leaves the modal
// drawing an empty `<ul class="diff-file-list">` with every other assertion
// in this file still green, since none of them mount a zero-file state.
// ---------------------------------------------------------------------------

test('a session with a clean worktree (zero changed files) shows "Nothing to show." instead of an empty file list', async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    const CLEAN_STATE = { is_git: true, has_head: true, files: [], additions: 0, deletions: 0 };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.endsWith('/api/agents/%401/diff')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CLEAN_STATE) });
        }
        return fakeFetch(url);
    };
    try {
        clickFilesChip(filesBtn);
        await flush();

        assert.equal(document.querySelectorAll('#diff-modal-content .diff-file-row').length, 0,
            'setup: a clean-worktree fixture rendered file rows');
        // 🔴 GUARD: proven by PRESENCE, not just absence — an empty
        // `<ul class="diff-file-list">` also has zero `.diff-file-row`
        // children, so the assertion above alone cannot tell "the empty
        // state rendered its message" from "the empty state was dead-coded
        // into rendering nothing at all".
        const pane = document.querySelector('.diff-file-pane');
        assert.ok(pane, 'no .diff-file-pane element was rendered');
        assert.match(pane.innerHTML, /diff-file-list-empty/,
            'a clean worktree did not render the .diff-file-list-empty element — _fileListHtml\'s empty-state branch is dead-coded (or unreached)');
        assert.match(pane.textContent, /Nothing to show\./,
            'a clean worktree did not render "Nothing to show." — _fileListHtml\'s empty-state message is missing or dead-coded');
        window.chela.closeDiffModal();
    } finally {
        globalThis.fetch = originalFetch;
    }
});

// ---------------------------------------------------------------------------
// Round 9 (2026-08-17, PR #373 judge round 9) — the SIBLING empty state to
// the one above: _fileListHtml's "Nothing to show." covers zero CHANGED
// FILES; this one covers a single file whose patch text itself is empty — a
// zero-byte untracked file (`touch newfile.py`) makes
// `git diff --no-index -- /dev/null <path>` emit nothing, so file_patch
// returns {ok: true, patch: ''}. Every PATCH_TEXT fixture elsewhere in this
// file is non-empty, so nothing else in this suite ever reaches
// _patchHtml's `if (!patchText)` branch.
// ---------------------------------------------------------------------------

test('an empty patch (a zero-byte file) shows the "No diff text" empty state, not a blank pane', async () => {
    const filesBtn = document.querySelector('.term-ctx-bar[data-ctx-for="@1"] .gs-files');
    const originalFetch = globalThis.fetch;
    globalThis.fetch = url => {
        const path = String(url);
        if (path.startsWith('/api/agents/%401/diff/patch')) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, patch: '' }) });
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

        // 🔴 GUARD: `if (false && !patchText) return '<div class="diff-patch-empty">...'`
        // dead-codes this branch — every other fixture in this file returns a
        // non-empty patch, so nothing else can arm it. Dead-coded, an empty
        // patch string falls through to the line-by-line renderer, which
        // produces a blank pane — visually indistinguishable from a render
        // that silently failed — instead of the designed empty-state message.
        assert.match(patchView.textContent, /No diff text for this file\./,
            'an empty patch did not render the "No diff text for this file." empty state — _patchHtml\'s empty-patch branch is dead-coded (or unreached)');
        assert.match(patchView.innerHTML, /diff-patch-empty/,
            'an empty patch did not render the .diff-patch-empty element');
        window.chela.closeDiffModal();
    } finally {
        globalThis.fetch = originalFetch;
    }
});

// ---------------------------------------------------------------------------
// Round 10 (2026-08-18, CMX-306) — _ctxBarHTML has exactly TWO call sites in
// terminals.js: the wall tile (draggable=true, line ~2106 — every assertion
// above renders ONLY this one, since the fixture never leaves the default
// wall mode) and the single-pane / mobile pane (draggable=false, line
// ~1580 — desktop's Single-mode toggle and the forced-mobile render both
// funnel through this exact same branch). CMX-299 built the Files chip to be
// draggable-independent (it's spliced into the returned template with no
// `draggable ? … : ''` gate, unlike every other chip in that function), but
// nothing ever rendered the non-draggable branch to prove it — a regression
// that gated filesChip behind `draggable` (or dropped it from the
// non-draggable path entirely) would leave every test above this line green.
//
// Round 2 (judge): the round-10 test used a two-agent fixture with the
// select pinned to the SECOND agent, which is also the LAST agent — a
// mutation swapping the selected wid (`sw`) for `wids[wids.length - 1]` was
// indistinguishable from correct. It also never stubbed matchMedia to a
// phone width, so a mutation gating the chip behind `_isMobileTerm()` went
// unnoticed. Closed with a THIRD agent (so neither positional default
// equals the selection) and a mobile-width matchMedia stub for the whole
// test.
//
// Round 3 (judge): closing the mobile blind spot by stubbing matchMedia to
// PHONE for the *entire* test just traded it for the mirror desktop blind
// spot — a mutation gating the chip behind `!draggable && !_isMobileTerm()`
// (hides on DESKTOP, shows on phone) went unnoticed, because nothing here
// ever rendered the single pane at desktop width. And the three-agent
// fixture only proved the chip wasn't `wids[0]` or `wids[wids.length - 1]`
// — it never proved the chip TRACKS the selection, so a mutation that reads
// ANY fixed index into `wids` that happens to alias the selection's slot
// (`wids[1]`, since '@2' sits at index 1) was still indistinguishable from
// correct. Closed by rendering the single pane through THREE variants —
// (desktop, '@2'), (phone, '@2'), (phone, '@3') — asserting the chip's
// identity each time: the first two prove the chip survives a viewport flip
// in either direction with the SAME selection, and the third changes the
// selection (to a wid that is not `wids[1]`) and re-renders, which only a
// chip that reads the LIVE selection on every render — not a value fixed by
// position or captured once — can satisfy for all three. See
// docs/defeat_shapes/306-a-single-item-fixture-collapses-every-candidate-source.md
// (round 3 addendum) for the general shape.
//
// Round 4 (judge): "(desktop, '@2'), (phone, '@2'), (phone, '@3')" never
// actually rendered the middle variant. renderTerminals() memoizes on
// `sig = _termMode + '|' + sel.value + '|' + wids` (terminals.js:1540) and
// early-returns when `sig` is unchanged from the last render
// (terminals.js:1541) — and `sig` does NOT include the viewport. Variant 2
// kept `sel.value` at '@2' (same as variant 1) and only flipped the
// matchMedia stub, so its `sig` was byte-identical to variant 1's: the
// early-return fired, the stage was never rebuilt, and the assertions
// re-checked variant 1's stale DESKTOP DOM under a new label. Only two
// renders ever actually happened — (desktop, '@2') and (phone, '@3') —
// because those are the only two consecutive steps where `sel.value`
// changed. A wid source conditioned on the viewport (in either polarity)
// produces exactly the expected value at both of those: `sw` on desktop,
// `wids[wids.length - 1]` on phone. Closed by reordering the variants so
// EVERY consecutive step changes `sel.value` (never re-uses the previous
// one), which forces a real render every time regardless of viewport:
// (desktop, '@2') -> (phone, '@3') -> (phone, '@2') -> (desktop, '@3').
// This exercises all four (viewport, selection) combinations as genuine
// renders, closing both the viewport-gate and positional-index families in
// one pass, and also adds a same-node identity check so a future reordering
// that silently re-collapses `sig` fails loudly instead of re-asserting
// stale DOM. See
// docs/defeat_shapes/306-a-single-item-fixture-collapses-every-candidate-source.md
// (round 4 addendum) for the general shape.
//
// Round 5 (judge): two more gaps, neither about the fixture.
//
// [WIRING] All four variants above reach single-pane mode by hand-calling
// terminals.setTermMode('single') — none of them let PRODUCTION decide to
// enter single mode the way a real phone does. A real phone's persisted mode
// is 'wall' (the default, terminals.js:1519); renderTerminals()'s own
// force-single-below-768px branch (terminals.js:1512) is what flips
// _termMode to 'single' on that user's behalf. Dead-coding that branch
// (`if (false && matches...)`) was invisible to variants 1-4 because
// _termMode was ALREADY 'single' by the time any of them stubbed a phone
// viewport — the forcing branch is skipped either way, whether it's alive or
// dead. Closed with a fifth variant that puts the pane into a legitimate
// WALL render first (desktop width, so the forcing branch does NOT fire —
// proving that step is an unforced wall render), then stubs the viewport to
// phone and calls renderTerminals() with _termMode still 'wall' — the exact
// input state a real phone user's persisted mode + viewport produce. Only
// the production branch itself can turn that into a single-pane render.
//
// [MUTATION, absence-only mirror] Round 4's fix asserts .gs-idx/.gs-pr/
// .gs-cost are ABSENT to prove the non-draggable branch rendered — but never
// asserted the mirror: that elements which belong on BOTH branches (the
// model chip, the context-fill bar) are actually PRESENT here. Those two are
// spliced with no `draggable ? … : ''` gate in source today, but nothing
// re-renders if one gets gated later — this is the ONLY place in the suite
// that renders _ctxBarHTML(wid, false) at all. Closed by asserting .gs-model
// and .term-ctx-fill are present on every variant's bar, not just absent on
// the draggable-only siblings. See
// docs/defeat_shapes/306-a-single-item-fixture-collapses-every-candidate-source.md
// (round 5 addendum) for the general shape.
// ---------------------------------------------------------------------------

test('the Files chip is also emitted (and wired end to end) in single-pane / mobile mode, not just the wall tile — at both desktop and phone widths, and it tracks the live selection rather than any fixed position', async () => {
    const modal = document.getElementById('modal-diff');
    const sel = document.getElementById('term-agent');
    const originalFetch = globalThis.fetch;
    const originalMatchMedia = window.matchMedia;
    try {
        // THREE agents. '@2' (the initial selection) sits at index 1, so it
        // is neither `wids[0]` nor `wids[wids.length - 1]` — and '@3' (the
        // selection this test switches to below) is neither `wids[1]` nor
        // any OTHER fixed index that could have coincidentally matched '@2'
        // above, since it is a different agent at a different render.
        util.setAgentsCache([...AGENTS,
            { name: 'w2', window_id: '@2', online: true },
            { name: 'w3', window_id: '@3', online: true }]);

        globalThis.fetch = url => {
            const path = String(url);
            if (path.endsWith('/api/agents/%402/diff') || path.endsWith('/api/agents/%403/diff')) {
                return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(DIFF_STATE) });
            }
            return fakeFetch(url);
        };

        const stubViewport = isPhone => {
            window.matchMedia = q => ({
                media: q, matches: isPhone, addEventListener() {}, removeEventListener() {},
                addListener() {}, removeListener() {},
            });
        };

        // Render the single pane for one (viewport, selected wid) variant
        // and drive the FULL click -> modal -> file-list -> close chain,
        // asserting the chip's identity is `expectedWid` specifically — not
        // just "a" chip, so this catches both a viewport-conditioned gate
        // and a positional (not selection-driven) wid source.
        //
        // renderTerminals() memoizes on `sig = _termMode|sel.value|wids`
        // (terminals.js:1540) and no-ops when `sig` is unchanged — `sig`
        // does NOT include the viewport. `expectRebuild` records whether
        // THIS call's `sel.value` differs from the previous call's (i.e.
        // whether a real rebuild is actually forced); when it does, assert
        // the `.term-ctx-bar` node identity actually changed, so a future
        // reordering that lets two consecutive variants share `sel.value`
        // (silently collapsing them onto one stale render) fails loudly
        // instead of quietly re-asserting the previous variant's DOM.
        let previousBarNode = null;
        async function renderAndAssertSinglePane(isPhone, expectedWid, label, { expectRebuild }) {
            const selValueChanged = sel.value !== expectedWid;
            stubViewport(isPhone);
            sel.value = expectedWid;
            await terminals.renderTerminals();

            const stage = document.getElementById('term-stage');
            assert.ok(stage.classList.contains('term-single'),
                `${label}: renderTerminals() did not stay in single-pane render`);
            assert.equal(document.querySelectorAll('.term-ctx-bar').length, 1,
                `${label}: single-pane mode rendered more (or fewer) than exactly one context bar`);

            const barNode = document.querySelector('.term-ctx-bar');
            if (expectRebuild) {
                assert.ok(selValueChanged,
                    `${label}: test bug — expectRebuild:true requires this call's selection to differ from the previous call's, or renderTerminals()'s sig-based memoization (terminals.js:1540) will legitimately no-op`);
                assert.notStrictEqual(barNode, previousBarNode,
                    `${label}: renderTerminals() reused the previous variant's stale DOM node instead of rebuilding the stage — this variant's assertions would silently re-check the WRONG render (sig memoization coalesced it with the prior call)`);
            }
            previousBarNode = barNode;

            // _ctxBarHTML(wid, draggable) has exactly two callers: the wall
            // tile (draggable=true, terminals.js:2106) and this single-pane
            // path (draggable=false, terminals.js:1580). Only the draggable
            // branch emits .gs-idx/.gs-pr/.gs-cost (terminals.js:2233,2243-
            // 2245); the Files chip itself is draggable-independent, so
            // asserting it alone can't tell "the non-draggable branch
            // rendered" from "the call site silently started passing
            // draggable=true" — both leave the chip present and correctly
            // wired. Assert the draggable-only siblings are ABSENT so a
            // call-site regression to draggable=true (which also leaks an
            // Alt+N tooltip and PR/cost chips into the compact mobile bar,
            // per style.css:3764) goes red here even though the Files chip
            // itself would look unchanged.
            assert.equal(barNode.querySelector('.gs-idx'), null,
                `${label}: the single-pane bar rendered .gs-idx — that element only belongs to _ctxBarHTML's DRAGGABLE (wall-tile) branch; its presence here means the single-pane call site regressed to draggable=true, silently un-rendering the non-draggable branch this guard exists to cover`);
            assert.equal(barNode.querySelector('.gs-pr'), null,
                `${label}: the single-pane bar rendered .gs-pr — that element only belongs to _ctxBarHTML's DRAGGABLE (wall-tile) branch; its presence here means the single-pane call site regressed to draggable=true, silently un-rendering the non-draggable branch this guard exists to cover`);
            assert.equal(barNode.querySelector('.gs-cost'), null,
                `${label}: the single-pane bar rendered .gs-cost — that element only belongs to _ctxBarHTML's DRAGGABLE (wall-tile) branch; its presence here means the single-pane call site regressed to draggable=true, silently un-rendering the non-draggable branch this guard exists to cover`);

            // Mirror of the three absence checks above: .gs-model and
            // .term-ctx-fill are NOT draggable-gated in source (they belong
            // on every surface — terminals.js:2234-2237 for the model chip;
            // the fill is unconditional), so a regression that adds a
            // `draggable ? … : ''` gate to either one leaves the absence
            // checks above untouched (they only watch the DRAGGABLE-only
            // siblings) and would ship silently without this pair.
            assert.ok(barNode.querySelector('.gs-model'),
                `${label}: the single-pane bar has no .gs-model chip — it must ride on every surface, wall tiles AND the single/mobile pane (terminals.js:2234-2237, Liav 2026-07-25); its absence means a draggable-only gate crept onto a chip that's supposed to be shared`);
            const fillEl = barNode.querySelector('.term-ctx-fill');
            assert.ok(fillEl,
                `${label}: the single-pane bar has no .term-ctx-fill element — _applyTermContext hard-requires it before painting ANYTHING into the bar (terminals.js:1348-1350: "const fill = bar.querySelector('.term-ctx-fill'); if (!fill) return;"), so its absence silently kills this bar's entire context repaint (branch chip, context %, model chip, tooltip) forever, not just a 2px fill`);

            const filesBtn = document.querySelector(`.term-ctx-bar[data-ctx-for="${expectedWid}"] .gs-files`);
            assert.ok(filesBtn,
                `${label}: the single-pane bottom bar has no .gs-files "Files" chip for ${expectedWid}, the SELECTED agent — either the chip was dropped on the non-draggable (single/mobile) path at this viewport, or the pane rendered a positional (not selected) agent`);
            assert.match(filesBtn.getAttribute('onclick') || '', new RegExp(`chela\\.openDiffModal\\('${expectedWid}'\\)`),
                `${label}: the single-pane Files chip is wired to the wrong session — it must open ${expectedWid} (the DISPLAYED/selected agent), not a positional default`);
            assert.equal(filesBtn.getAttribute('title'), 'Changed files',
                `${label}: the single-pane Files chip lost its "Changed files" title`);
            assert.ok(/<circle|<path/.test(filesBtn.innerHTML),
                `${label}: the single-pane Files chip rendered no SVG glyph content — the git-compare icon is empty`);

            assert.equal(modal.classList.contains('active'), false,
                `${label}: the diff modal starts open — the click assertion below could not tell a real open from a no-op`);
            clickFilesChip(filesBtn);
            assert.equal(modal.classList.contains('active'), true,
                `${label}: clicking the single-pane Files chip never made #modal-diff visible — the non-draggable path's chip is present but dead`);

            await flush();   // let openDiffModal's /diff fetch + _render resolve
            const rows = document.querySelectorAll('#diff-modal-content .diff-file-row');
            assert.equal(rows.length, 2,
                `${label}: the diff modal did not render ${expectedWid}'s fetched file list when opened from single-pane mode — either the wrong session's /diff was requested, or none was`);

            window.chela.closeDiffModal();
            assert.equal(modal.classList.contains('active'), false,
                `${label}: closeDiffModal() did not close the modal that was opened from single-pane mode`);
        }

        // Desktop width, selection = '@2'. Establishes single-pane mode via
        // setTermMode (which fires renderTerminals() without awaiting it
        // internally — one flush() lands after its readiness Promise.all,
        // same reasoning as before()'s initial render sharing the fake
        // ttyd-ready fixture); every later variant re-renders directly. This
        // first variant's `sel.value` matches what setTermMode already
        // established, so it does NOT force a fresh rebuild on its own
        // (expectRebuild: false) — it's re-asserting the state setTermMode
        // just produced, which is fine.
        stubViewport(false);
        sel.value = '@2';
        terminals.setTermMode('single');
        await flush();
        await renderAndAssertSinglePane(false, '@2', 'desktop width, @2 selected', { expectRebuild: false });

        // Every variant from here on changes `sel.value` from the IMMEDIATELY
        // PRECEDING call, so each one forces a real rebuild through
        // renderTerminals()'s sig-based memoization regardless of the
        // viewport — a viewport-only flip (keeping the same selection, as
        // round 3's ordering did) can no-op and silently re-assert stale
        // DOM (round 4's finding). Walking
        // (desktop,'@2') -> (phone,'@3') -> (phone,'@2') -> (desktop,'@3')
        // exercises all four (viewport, selection) combinations as genuine
        // renders: a chip gated on `_isMobileTerm()` in either polarity
        // dies at one of the two same-viewport-different-selection pairs
        // below, and a chip sourced from any fixed index into `wids` (not
        // the live selection) dies the moment the selection flips back from
        // '@3' to '@2' without a matching viewport change to hide behind.
        await renderAndAssertSinglePane(true, '@3', 'phone width, @3 selected (selection changed)', { expectRebuild: true });
        await renderAndAssertSinglePane(true, '@2', 'phone width, @2 selected (selection changed back)', { expectRebuild: true });
        await renderAndAssertSinglePane(false, '@3', 'desktop width, @3 selected (selection changed)', { expectRebuild: true });

        // Round 5 (judge): every variant above REACHES single-pane mode via a
        // direct terminals.setTermMode('single') call — none of them let
        // PRODUCTION decide. A real phone's persisted mode is 'wall' (the
        // default); renderTerminals()'s own force-single-below-768px branch
        // (terminals.js:1512) is what puts that user's viewport into single
        // mode. First put the pane into a LEGITIMATE wall render at desktop
        // width — proving this step doesn't itself trip the forcing branch —
        // then flip to a phone viewport and re-render with _termMode still
        // 'wall', the exact input state a real phone user's persisted mode +
        // viewport produce. Only the forcing branch itself (not any test
        // helper) can turn that into the single-pane render this whole guard
        // is about.
        stubViewport(false);
        terminals.setTermMode('wall');
        await flush();
        assert.equal(document.getElementById('term-stage').classList.contains('term-single'), false,
            'setup for the production force-single variant: desktop width did not stay in wall mode — the test fixture itself is broken, not the branch under test');

        await renderAndAssertSinglePane(true, '@2',
            "production force-single path: renderTerminals() itself must flip persisted 'wall' mode to single below 768px — not a direct setTermMode('single') call",
            { expectRebuild: true });
    } finally {
        globalThis.fetch = originalFetch;
        window.matchMedia = originalMatchMedia;
        // restore the single-agent, wall-mode fixture every other test in this file depends on
        util.setAgentsCache(AGENTS);
        terminals.setTermMode('wall');
        await flush();
    }
});
