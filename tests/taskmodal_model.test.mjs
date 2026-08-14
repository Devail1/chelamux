// TASK-DETAIL MODAL MODEL — pure brief-markdown + review-timeline guards
// (chela/dashboard/static/js/taskmodalmodel.js). No DOM: these are straight
// function-of-inputs checks, each written to go RED under one specific
// corruption of the real logic (a guard that survives its own corruption is
// decoration, not a guard).
//
// Run: node --test tests/taskmodal_model.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { before, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

let tm, knowledge;

before(async () => {
    // taskmodalmodel.js imports knowledge.js's knMd, which imports util.js —
    // util.js reads window/document at MODULE SCOPE (window.location.pathname,
    // document.addEventListener, ...), so those globals must exist before the
    // import, same bootstrap as the now-deleted tests/knowledge_graph.test.mjs.
    const dom = new JSDOM('<!doctype html><html><body></body></html>',
        { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'getComputedStyle', 'HTMLElement', 'Element', 'Node']) {
        Object.defineProperty(globalThis, k, {
            value: dom.window[k], writable: true, configurable: true,
        });
    }
    globalThis.window.chela = globalThis.window.chela || {};
    tm = await import('../chela/dashboard/static/js/taskmodalmodel.js');
    knowledge = await import('../chela/dashboard/static/js/knowledge.js');
});

// --- briefSource: brief > body > raw, never throws on a sparse item --------

test('briefSource: a run brief wins over body and raw', () => {
    // 🔴 GUARD: reordering the priority list (e.g. checking `body` first) would
    // make a claimed run with a stale/short `body` shadow its own richer,
    // claim-time-persisted `brief` — this pins brief as authoritative once a
    // run exists.
    const item = { brief: 'RUN BRIEF', body: 'TASK BODY', raw: '- [ ] raw line' };
    assert.equal(tm.briefSource(item), 'RUN BRIEF');
});

test('briefSource: falls back to body when there is no brief (an open task)', () => {
    const item = { brief: null, body: 'TASK BODY', raw: '- [ ] raw line' };
    assert.equal(tm.briefSource(item), 'TASK BODY');
});

test('briefSource: falls back to raw when neither brief nor body exist', () => {
    // 🔴 GUARD: a bare one-line task (chela.sources.markdown._task_body returns
    // None for it) or a gh_issues task (no `body` concept at all) must still
    // render SOMETHING — dropping this fallback step would blank the pane for
    // every task that predates this feature.
    const item = { brief: null, body: null, raw: '- [ ] raw line' };
    assert.equal(tm.briefSource(item), '- [ ] raw line');
});

test('briefSource: empty strings are treated as absent, not as a match', () => {
    // 🔴 GUARD: using `??`/`||` naively on an EMPTY STRING `brief` would return
    // `''` (falsy-but-present) instead of falling through to `body` — dropping
    // the explicit `v !== ''` check reproduces that.
    const item = { brief: '', body: '', raw: 'raw wins' };
    assert.equal(tm.briefSource(item), 'raw wins');
});

test('briefSource: nothing at all (a backlog item) resolves to null, never throws', () => {
    assert.equal(tm.briefSource({}), null);
    assert.equal(tm.briefSource(null), null);
    assert.equal(tm.briefSource(undefined), null);
});

// --- briefHtml: pins the knMd CONTRACT the brief pane depends on -----------

test('briefHtml: empty/null/undefined text renders nothing', () => {
    // Base-case pin, not a corruption-sensitive guard on its own (knMd's own
    // `src || ''` already tolerates null/undefined, so dropping briefHtml's
    // `if (!text)` short-circuit does not by itself go RED here) — it exists so
    // taskmodal.js's "no brief" fallback path (openTaskModal calling this with
    // an empty string) has a pinned, explicit expectation. The heading/list/code
    // test below is what actually goes RED on a real regression (see its comment).
    assert.equal(tm.briefHtml(''), '');
    assert.equal(tm.briefHtml(null), '');
    assert.equal(tm.briefHtml(undefined), '');
});

test('briefHtml: a heading + numbered list + inline code render via knMd', () => {
    const src = '### OBJECTIVE\nBuild `sample()` with these steps:\n1. First step with `code`.\n2. Second step.\n\nSome paragraph.\n';
    const html = tm.briefHtml(src);
    // 🔴 GUARD: this is the EXACT knMd output for this fixture (verified against
    // knowledge.js directly) — taskmodal.js's brief pane depends on this shape:
    // a `#{1,4}` heading renders <h3 class="kn-mh">, inline `` `code` `` becomes
    // <code>, and a `1.`/`2.` run renders as one <ol class="kn-ol"> with the
    // numeral stripped from each <li>. Regressing ANY of these (heading level,
    // code wrapping, dropping ordered-list support, or emitting <ul> instead of
    // <ol> for numbers) changes this string and goes RED.
    assert.equal(
        html,
        '<h3 class="kn-mh">OBJECTIVE</h3>'
        + '<p>Build <code>sample()</code> with these steps:</p>'
        + '<ol class="kn-ol"><li>First step with <code>code</code>.</li><li>Second step.</li></ol>'
        + '<p>Some paragraph.</p>',
    );
});

// --- knMd: the bullet-list branch — guards lost when tests/knowledge_graph.test.mjs
// was deleted alongside the Knowledge view (CMX-279). knMd is SHARED, not
// exclusive to that view (see knowledge.js's own header: it backs THIS file's
// briefHtml too), but its tests lived only in the deleted view's test file, so
// deleting that file silently dropped every guard on this branch — the
// exact-output test above never contains a `-`/`*` bullet, only `1.`/`2.`. -----

test('knMd: a `-` run renders <ul> with a MATCHING closing tag, not <ol>', () => {
    // 🔴 GUARD (CMX-279 rework round 2, PR #350): the briefHtml exact-output test
    // above only ever exercises the ORDERED (`1.`/`2.`) branch. A corruption that
    // opens `<ol class="kn-ol">` for a `-` run while closeList() still emits
    // `</ul>` (listType is still set to 'ul') produces a byte-identical
    // `<ol class="kn-ol">...</ul>` mismatch — a substring check on either tag
    // alone would miss it; only asserting the FULL string (open tag through
    // close tag) catches the mismatch.
    assert.equal(
        knowledge.knMd('- one\n- two'),
        '<ul class="kn-ul"><li>one</li><li>two</li></ul>',
    );
});

test('knMd: a heading between a `-` run and a `1.` run splits them into two separate lists', () => {
    // 🔴 GUARD: dropping closeList() from the heading branch would merge the
    // bullet run and the numbered run into one list (or leave a dangling open
    // tag) instead of two independently-closed ones.
    assert.equal(
        knowledge.knMd('- a\n### H\n1. b'),
        '<ul class="kn-ul"><li>a</li></ul><h3 class="kn-mh">H</h3><ol class="kn-ol"><li>b</li></ol>',
    );
});

test('knMd: switching list kind mid-run (bullet then numbered, no blank line) closes the first list before opening the second', () => {
    // 🔴 GUARD: this is what `listType` (tracking 'ul' vs 'ol' instead of a
    // single boolean) exists for — collapsing it back to a boolean would merge
    // an adjacent `-` run and `1.` run into one mismatched list.
    assert.equal(
        knowledge.knMd('- a\n1. b'),
        '<ul class="kn-ul"><li>a</li></ul><ol class="kn-ol"><li>b</li></ol>',
    );
});

// --- knInline's own rules — bold + links (round 3, PR #350) ----------------
//
// Every guard above that reaches knInline only ever does so through `` `code` ``
// (the briefHtml exact-output fixture) or list structure (the three tests just
// above) — nothing anywhere fed knMd/knInline a `**bold**` span or a `[text](href)`
// link, so both of knInline's other two rules, and knLink's branch on `href`,
// were free to break with the full suite staying green. This one fixture pins
// all three at once: a bold span mid-line, an external link, an in-bundle `.md`
// link (knLink's rewritten branch — CMX-279 deleted the Knowledge concept
// browser this used to route `.md` targets to), and a `#anchor` link (the ONE
// case that must NOT become a clickable link, since there is nothing left in
// the app for it to open).
test('knMd: a bold span, an external link, an in-bundle .md link, and a #anchor link all render via knInline/knLink', () => {
    const src = '- ship **the wall** now\n'
        + '- see [docs](https://example.com/x) and [notes](foo.md) but not [here](#anchor)';
    assert.equal(
        knowledge.knMd(src),
        '<ul class="kn-ul">'
        + '<li>ship <strong>the wall</strong> now</li>'
        + '<li>see <a href="https://example.com/x" target="_blank" rel="noopener">docs</a>'
        + ' and <a href="foo.md" target="_blank" rel="noopener">notes</a>'
        + ' but not here</li>'
        + '</ul>',
    );
});

// --- knMd: blockquote + fenced code — the other two branches named in round
// 2's note (the bullet-list branch was closed in round 2, bold/links in round
// 3; blockquote and fenced code were still exactly where the note left them
// going into round 4 — DEFEAT_SHAPES #19) --------------------------------

test('knMd: a blockquote line and a fenced code block render via their own branches, not as plain paragraphs, and fenced content is NOT run through knInline', () => {
    // 🔴 GUARD (CMX-279 rework round 4, PR #350): dead-coding either branch's
    // `if` condition (`if (false && ...)`) makes the `>`/``` lines fall through
    // to the plain-paragraph case instead — no <blockquote>, no <pre class="kn-code">.
    // The `**not bold**` line INSIDE the fence also pins that fenced lines are
    // escaped verbatim (escHtml), not run through knInline — if the fence
    // detector is dead-coded, that line gets knInline'd instead and comes out
    // as `<strong>not bold</strong>`.
    const src = '> quoted line\n```\nconst x = 1;\n**not bold**\n```\nafter';
    assert.equal(
        knowledge.knMd(src),
        '<blockquote>quoted line</blockquote>'
        + '<pre class="kn-code"><code>const x = 1;\n**not bold**\n</code></pre>'
        + '<p>after</p>',
    );
});

// --- knInline: escHtml — the PR's own claim (kanban.js's comment, round 3's
// verdict) that knInline is where HTML-special characters get escaped on this
// path. No fixture anywhere in the suite fed knInline anything containing
// `<`, `>` or `&`, so dropping the escHtml call entirely stayed green. -----

test('knMd: HTML-special characters are escaped by knInline before any markdown rule runs', () => {
    // 🔴 GUARD (CMX-279 rework round 4, PR #350): replacing `s = escHtml(s)`
    // with a bare null-guard (`s = (s == null) ? '' : String(s)`) leaves
    // `<img src=x onerror=alert(1)>` un-escaped — this is exactly the string a
    // task title or brief would splice raw into the kanban card / modal
    // header / brief pane.
    assert.equal(
        knowledge.knMd('<img src=x onerror=alert(1)> & unescaped'),
        '<p>&lt;img src=x onerror=alert(1)&gt; &amp; unescaped</p>',
    );
});

// --- knLink: attrEsc on the href — round 3 added a link fixture, but neither
// of its hrefs contains a `"`, so attrEsc's one job over plain escHtml (that
// a quote can't break out of the attribute) was asserted by nothing. -------

test('knMd: a link href containing a double quote is escaped by attrEsc, not spliced raw into the attribute', () => {
    // 🔴 GUARD (CMX-279 rework round 4, PR #350): replacing `attrEsc(href)`
    // with a bare `href` (bypassing attrEsc while keeping the binding
    // referenced) leaves the `"` in the href literal, producing
    // `href="foo"bar"` — a link target that injects arbitrary attributes into
    // the rendered <a>.
    assert.equal(
        knowledge.knMd('[text](foo"bar)'),
        '<p><a href="foo&quot;bar" target="_blank" rel="noopener">text</a></p>',
    );
});

// --- displayTitle: display-only concise header, never the parsed title -----

test('displayTitle: a leading bold span becomes the display title, trailing text dropped', () => {
    // 🔴 GUARD: this is the shape our briefs actually have — the parsed `title`
    // is the WHOLE bullet line (title-hash = task id, so it is never rewritten;
    // see taskmodalmodel.js's doc comment). Corrupt this by dropping the
    // bold-extract (return rawTitle unchanged) and this fails because the raw
    // `**`/trailing sentence would leak through; corrupt it the other way
    // (return the trailing text instead of the captured group) and the
    // assertion also fails, since it would no longer equal the bold span.
    const raw = '**📥 Move the Decisions log to the wiki.** Design is SETTLED — build EXACTLY as spec\'d.';
    assert.equal(tm.displayTitle(raw), '📥 Move the Decisions log to the wiki.');
});

test('displayTitle: a title with no leading bold span is returned unchanged', () => {
    assert.equal(tm.displayTitle('plain one-line task title'), 'plain one-line task title');
});

test('displayTitle: bold text elsewhere (not leading) is not extracted', () => {
    // Only a bold span that STARTS the string counts as "the concise title" —
    // a `**bold**` in the middle of a plain-text title is not the same shape.
    assert.equal(tm.displayTitle('do the thing with **emphasis** in the middle'),
        'do the thing with **emphasis** in the middle');
});

test('displayTitle: empty/null/undefined resolve gracefully, never throw', () => {
    assert.equal(tm.displayTitle(''), '');
    assert.equal(tm.displayTitle(null), '');
    assert.equal(tm.displayTitle(undefined), '');
});

// --- timelineSteps: never throws, always an ordered list of {state, detail} --

test('timelineSteps: null/empty/malformed JSON all resolve to [], never throw', () => {
    // The empty-guard (`if (!reviewHistoryJson) return [];`) covers the common
    // case — a brand-new run with no review yet — as a cheap branch before ever
    // calling JSON.parse. null/''/undefined all take it.
    assert.deepEqual(tm.timelineSteps(null), []);
    assert.deepEqual(tm.timelineSteps(''), []);
    assert.deepEqual(tm.timelineSteps(undefined), []);
    // 🔴 GUARD: '{not json' is NOT falsy, so it skips the empty-guard and reaches
    // JSON.parse, which throws a SyntaxError — remove the try/catch around that
    // parse (or replace it with a bare `JSON.parse(reviewHistoryJson)`) and this
    // specific assertion goes RED with an uncaught SyntaxError (verified).
    assert.deepEqual(tm.timelineSteps('{not json'), []);
});

test('timelineSteps: well-formed JSON that is not a list resolves to []', () => {
    // 🔴 GUARD: drop the `if (!Array.isArray(parsed)) return [];` guard and a
    // bare object payload reaches `.filter`, which throws (objects have no
    // .filter) — this is the corruption to try when verifying the guard.
    assert.deepEqual(tm.timelineSteps('{"round":1}'), []);
    assert.deepEqual(tm.timelineSteps('"just a string"'), []);
    assert.deepEqual(tm.timelineSteps('42'), []);
});

test('timelineSteps: non-object array entries are dropped, valid ones map to {state, detail}, order preserved', () => {
    const raw = JSON.stringify([
        { round: 1, at: '2026-07-20T10:00:00+00:00', body: 'fix the flaky test', verdict: 'changes_requested' },
        'a stray string entry',          // dropped
        null,                            // dropped
        42,                              // dropped
        { round: 2, at: '2026-07-20T11:00:00+00:00', body: 'looks good now', verdict: 'reopened' },
    ]);
    const steps = tm.timelineSteps(raw);
    assert.equal(steps.length, 2, 'the 3 malformed entries must be dropped, not crash or pass through');
    assert.deepEqual(steps[0], {
        round: 1, at: '2026-07-20T10:00:00+00:00', state: 'changes_requested', detail: 'fix the flaky test',
    });
    assert.deepEqual(steps[1], {
        round: 2, at: '2026-07-20T11:00:00+00:00', state: 'reopened', detail: 'looks good now',
    });
});

test('timelineSteps: a missing verdict/body degrades to a safe default, not undefined/crash', () => {
    const raw = JSON.stringify([{ round: 1, at: 't' }]);
    const steps = tm.timelineSteps(raw);
    assert.equal(steps.length, 1);
    assert.equal(steps[0].state, 'review', 'no verdict on the row -> a generic, non-empty state label');
    assert.equal(steps[0].detail, '', 'no body on the row -> empty detail, never undefined (would render "undefined")');
});
