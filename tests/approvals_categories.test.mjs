// ⚖️🔔 CMX-103 r4 — the CATEGORY pass over the Pending-approvals sheet, in a real DOM.
//
// The judge deleted `escHtml(r.reason)` and every test stayed green: the suite had asserted
// the happy text of a few fields and never that a field is ESCAPED. So, per category:
//   A. every request-supplied field that the pill / sheet renders is injected with markup and
//      must land as TEXT (no element, no handler attribute); an href is http(s)-only;
//   B. durations and the badge text are pinned to LITERALS;
//   D. rows keep the server's order (oldest first); both kinds render their own fields;
//   E. a failed fetch keeps what was known — it never turns into "nothing is waiting".
//
// Run: node --test tests/approvals_categories.test.mjs (pytest runs it via test_js_suites.py).
import { before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let LISTING = { requests: [] };
let FAIL = false;
let POST_REPLY = null;

function fakeFetch(url, opts) {
    if (FAIL) return Promise.reject(new Error('network down'));
    const method = (opts && opts.method) || 'GET';
    const body = method === 'POST' ? (POST_REPLY || { ok: true, state: 'denied' }) : LISTING;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

let pa;
before(async () => {
    const dom = new JSDOM(`<!doctype html><html><body><button id="btn-approvals" hidden><span class="pa-pill-text"></span></button></body></html>`,
        { url: 'http://localhost:5005/' });
    for (const k of ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'Event', 'KeyboardEvent']) {
        Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    }
    globalThis.fetch = fakeFetch;
    pa = await import('../chela/dashboard/static/js/approvals.js');
});

beforeEach(() => {
    FAIL = false; POST_REPLY = null; LISTING = { requests: [] };
    pa.closeApprovals();
});

const ID = 'override-' + 'a'.repeat(24);
const req = (over) => ({ id: ID, kind: 'override', task_id: 't1', label: 'cmx-1', head_sha: 'b'.repeat(40),
    judge_state: 'blocked', base: 'dev', reason: 'judge flaked', actor: 'orchestrator', age_s: 120,
    expires_in_s: 3600, blocked: null, pr_url: 'https://github.com/o/r/pull/1', ...over });
const tick = () => new Promise(r => setTimeout(r, 0));

// --- A. escaping -------------------------------------------------------------------------

const IMG = '<img src=x onerror=alert(1)>';
const SCRIPT = '"><script>alert(1)</script>';
const SHORT = '<i id=pwn>';            // fits a 12-char slice (head_sha is truncated to 12)
const ATTR = '" onmouseover="alert(1)" x="';

function assertInert(where) {
    assert.equal(where.querySelector('img, script, [onerror], [onmouseover], #pwn'), null,
        'a request field created an element / handler');
    // (not a string search of innerHTML: an attribute value legitimately serialises a `<`)
}

// field -> the request overrides that make the sheet render it
const FIELDS = {
    reason: (p) => ({ reason: p }),
    label: (p) => ({ label: p }),
    task_id: (p) => ({ label: null, task_id: p }),
    head_sha: (p) => ({ head_sha: p }),
    judge_state: (p) => ({ judge_state: p }),
    base: (p) => ({ base: p }),
    actor: (p) => ({ actor: p }),
    blocked: (p) => ({ blocked: p }),
    version: (p) => ({ kind: 'release', version: p }),
    release_label: (p) => ({ kind: 'release', version: null, label: p }),
    question: (p) => ({ kind: 'ask', question: p }),
    kind: (p) => ({ kind: p, question: null }),
    pr_url: (p) => ({ pr_url: p }),
    id: (p) => ({ id: p }),
};

for (const [field, make] of Object.entries(FIELDS)) {
    for (const payload of [IMG, SCRIPT, SHORT, ATTR]) {
        test(`${field}: ${payload} renders as text, never as markup`, async () => {
            LISTING = { requests: [req(make(payload))] };
            await pa.openApprovals();
            const sheet = document.getElementById('approvals-backdrop');
            assertInert(sheet);
            assertInert(document.getElementById('btn-approvals'));
        });
    }
}

test('the text fields show the payload literally', async () => {
    LISTING = { requests: [req({ reason: IMG, actor: SCRIPT, label: SHORT })] };
    await pa.openApprovals();
    const row = document.querySelector('.pa-row');
    assert.equal(row.querySelector('.pa-why').textContent, '“' + IMG + '”');
    assert.ok(row.querySelector('.pa-meta').textContent.includes('asked by ' + SCRIPT));
    assert.ok(row.querySelector('.pa-what').textContent.includes(SHORT));
});

test('a refused decision reason is escaped on the sheet', async () => {
    LISTING = { requests: [req()] };
    await pa.openApprovals();
    POST_REPLY = { ok: false, state: 'failed', detail: IMG };
    document.querySelector('.pa-deny').click();
    await tick(); await tick();
    const err = document.querySelector('#approvals-backdrop .pa-err');
    assert.equal(err.textContent, IMG);
    assertInert(document.getElementById('approvals-backdrop'));
});

test('an id with a quote cannot break out of data-id and the deny URL is encoded', async () => {
    const bad = 'x" onclick="alert(1)';
    LISTING = { requests: [req({ id: bad })] };
    await pa.openApprovals();
    const row = document.querySelector('.pa-row');
    assert.equal(row.dataset.id, bad);
    assert.equal(row.querySelector('[onclick]'), null);
});

test('a PR url is an http(s) link opened safely', async () => {
    LISTING = { requests: [req({ pr_url: 'https://github.com/o/r/pull/1' })] };
    await pa.openApprovals();
    const a = document.querySelector('.pa-meta a');
    assert.equal(a.getAttribute('href'), 'https://github.com/o/r/pull/1');
    assert.equal(a.getAttribute('rel'), 'noopener');
    assert.equal(a.getAttribute('target'), '_blank');
});

test('a quote in the PR url stays inside the href attribute', async () => {
    LISTING = { requests: [req({ pr_url: 'https://x.test/"><img src=x onerror=alert(1)>' })] };
    await pa.openApprovals();
    assert.equal(document.querySelectorAll('.pa-meta a').length, 1);
    assert.equal(document.querySelector('.pa-meta a').getAttribute('href'),
        'https://x.test/"><img src=x onerror=alert(1)>');
});

// A PR url is agent-supplied: only http(s) becomes a link; anything else is plain escaped text.
for (const url of ['javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', '\u0001javascript:alert(1)',
    'java\nscript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'ftp://x.test/a', '//evil.test/x', 'vbscript:x',
    // http(s):// must be the PREFIX, not merely present somewhere in the url (CMX-103 round 6).
    'javascript:alert(1)//https://x.test', 'data:text/html,x#http://x.test', ' x https://x.test']) {
    test(`PR url ${JSON.stringify(url)} never becomes a live link`, async () => {
        LISTING = { requests: [req({ pr_url: url })] };
        await pa.openApprovals();
        assert.equal(document.querySelector('.pa-meta a'), null, 'a non-http(s) url became a link');
        assert.equal(document.querySelector('[href]'), null);
        assert.ok(document.querySelector('.pa-meta .pa-prtext').textContent.includes('PR: '), 'the url is not shown as text');
        assertInert(document.getElementById('approvals-backdrop'));
    });
}

test('a http(s) url is still a link, however it is cased or padded', async () => {
    for (const [url, href] of [['HTTPS://github.com/o/r/pull/1', 'HTTPS://github.com/o/r/pull/1'],
        ['  http://h.test/x\n', 'http://h.test/x'], ['https://git\thub.com/x', 'https://github.com/x']]) {
        LISTING = { requests: [req({ pr_url: url })] };
        await pa.openApprovals();
        assert.equal(document.querySelector('.pa-meta a').getAttribute('href'), href);
    }
});

test('safeHref: pinned', () => {
    assert.equal(pa.safeHref('https://a.test'), 'https://a.test');
    assert.equal(pa.safeHref(' \u0001 javascript:1'), null);
    assert.equal(pa.safeHref('javascript:alert(1)//https://x.test'), null);
    assert.equal(pa.safeHref('mailto:a@b.test?https://x.test'), null);
    assert.equal(pa.safeHref(null), null);
    assert.equal(pa.safeHref(undefined), null);
    assert.equal(pa.safeHref(''), null);
});

// --- B. computed values ------------------------------------------------------------------

test('durStr: pinned literals', () => {
    const cases = [[null, ''], [undefined, ''], [0, '<1m'], [29, '<1m'], [30, '1m'], [60, '1m'],
        [90, '2m'], [3540, '59m'], [3600, '1h 0m'], [3660, '1h 1m'], [47 * 3600 + 59 * 60, '47h 59m'],
        [48 * 3600, '2d 0h'], [50 * 3600, '2d 2h'], [-5, '<1m']];
    for (const [s, want] of cases) assert.equal(pa.durStr(s), want, `durStr(${s})`);
});

test('the meta line carries the exact age and time left', async () => {
    LISTING = { requests: [req({ age_s: 90, expires_in_s: 3 * 3600 + 15 * 60 })] };
    await pa.openApprovals();
    assert.equal(document.querySelector('.pa-meta').textContent.split(' · ').slice(0, 3).join(' · '),
        'asked by orchestrator · 2m ago · expires in 3h 15m');
});

test('the badge text and aria-label are exact for 1 and N', async () => {
    const pill = () => document.getElementById('btn-approvals');
    LISTING = { requests: [req()] };
    await pa.tickApprovals();
    assert.equal(pill().textContent, '⚖️ 1 pending approval');
    assert.equal(pill().getAttribute('aria-label'), '1 approval request waiting, tap to review');
    LISTING = { requests: [req(), req({ id: 'override-' + 'b'.repeat(24) }), req({ id: 'override-' + 'c'.repeat(24) })] };
    await pa.tickApprovals();
    assert.equal(pill().textContent, '⚖️ 3 pending approvals');
    assert.equal(pill().getAttribute('aria-label'), '3 approval requests waiting, tap to review');
    LISTING = { requests: [] };
    await pa.tickApprovals();
    assert.equal(pill().hidden, true);
});

// --- D. order and kinds ------------------------------------------------------------------

test('rows keep the server order (oldest first) — the sheet never re-sorts', async () => {
    const ids = ['d', 'a', 'c', 'b'].map(c => 'override-' + c.repeat(24));
    LISTING = { requests: ids.map((id, i) => req({ id, label: 'r' + i, age_s: 4000 - i * 1000 })) };
    await pa.openApprovals();
    assert.deepEqual([...document.querySelectorAll('.pa-row')].map(r => r.dataset.id), ids);
});

test('an override and a release each show their own fields', async () => {
    LISTING = { requests: [req(), req({ id: 'override-' + 'e'.repeat(24), kind: 'release', version: '0.9.0',
        label: 'v0.9.0', head_sha: null, judge_state: null, base: null })] };
    await pa.openApprovals();
    const [ov, rel] = [...document.querySelectorAll('.pa-row .pa-what')].map(e => e.textContent);
    assert.equal(ov, 'Override-merge cmx-1 past the judge’s blocked on bbbbbbbbbbbb → dev');
    assert.equal(rel, 'Release 0.9.0');
});

test('an override with no judge verdict says so', () => {
    assert.equal(pa.whatText(req({ judge_state: null, base: null })),
        'Override-merge cmx-1 with no judge verdict on bbbbbbbbbbbb');
});

// --- E. fail closed ----------------------------------------------------------------------

test('a failed fetch keeps what was known and says it could not refresh', async () => {
    LISTING = { requests: [req()] };
    await pa.openApprovals();
    FAIL = true;
    await pa.tickApprovals();                       // must not throw
    assert.equal(document.querySelectorAll('.pa-row').length, 1);
    assert.equal(document.getElementById('btn-approvals').hidden, false);
    assert.equal(document.querySelector('.pa-stale').hidden, false);
    assert.match(document.querySelector('.pa-stale').textContent, /couldn’t refresh approvals/i);
    FAIL = false; LISTING = { requests: [req()] };
    await pa.tickApprovals();
    assert.equal(document.querySelector('.pa-stale').hidden, true, 'the notice outlived the failure');
});

test('a failure with nothing known is "could not refresh", never "Nothing is waiting"', async () => {
    await pa.tickApprovals();                       // a good, empty listing: nothing known
    FAIL = true;
    await pa.tickApprovals();
    const pill = document.getElementById('btn-approvals');
    assert.equal(pill.hidden, false);
    assert.match(pill.textContent, /couldn’t refresh approvals/);
    await pa.openApprovals();
    assert.ok(!document.getElementById('approvals-backdrop').textContent.includes('Nothing is waiting'));
    assert.equal(document.querySelector('.pa-stale').hidden, false);
});

for (const bad of [{ error: 'store unreadable', requests: [] }, { error: 'x' }, { requests: 'nope' }, null]) {
    test(`an error reply ${JSON.stringify(bad)} keeps the list`, async () => {
        LISTING = { requests: [req()] };
        await pa.openApprovals();
        LISTING = bad;
        await pa.tickApprovals();
        assert.equal(document.querySelectorAll('.pa-row').length, 1, 'an error reply emptied the list');
        assert.equal(document.querySelector('.pa-stale').hidden, false);
        assert.equal(document.getElementById('btn-approvals').hidden, false);
    });
}

test('an unreadable request file is shown, marked, with no Approve or Deny', async () => {
    const id = 'override-' + 'f'.repeat(24);
    LISTING = { requests: [req(), { id, kind: 'unreadable', reason: 'not valid JSON',
        blocked: 'unreadable request ' + id + ' — not valid JSON' }] };
    await pa.openApprovals();
    const rows = [...document.querySelectorAll('.pa-row')];
    assert.equal(rows.length, 2);
    const bad = rows[1];
    assert.equal(bad.querySelector('.pa-approve'), null);
    assert.equal(bad.querySelector('.pa-deny'), null);
    assert.equal(bad.querySelector('button'), null);
    assert.ok(bad.textContent.includes('unreadable request ' + id + ' — not valid JSON'));
    assert.match(document.getElementById('btn-approvals').textContent, /2 pending approvals/);
});

test('an unreadable row escapes its text', async () => {
    LISTING = { requests: [{ id: 'x', kind: 'unreadable', blocked: IMG + SHORT }] };
    await pa.openApprovals();
    assertInert(document.getElementById('approvals-backdrop'));
});

test('a page whose approvals endpoint has NEVER answered stays quiet on a dead fetch, but an explicit server error is shown', async () => {
    const dom = new JSDOM('<!doctype html><body><button id="btn-approvals" hidden><span class="pa-pill-text"></span></button></body>', { url: 'http://localhost:5005/' });
    const saved = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
    for (const k of Object.keys(saved)) Object.defineProperty(globalThis, k, { value: dom.window[k], writable: true, configurable: true });
    try {
        const fresh = await import('../chela/dashboard/static/js/approvals.js?never-answered');
        FAIL = true;
        await fresh.tickApprovals();
        assert.equal(document.getElementById('btn-approvals').hidden, true, 'a page with no approvals backend grew a pill');
        FAIL = false; LISTING = { requests: [], error: 'the approvals store cannot be read' };
        await fresh.tickApprovals();
        assert.equal(document.getElementById('btn-approvals').hidden, false, 'a server-reported error was hidden');
        assert.match(document.getElementById('btn-approvals').textContent, /couldn’t refresh approvals/);
    } finally {
        for (const [k, v] of Object.entries(saved)) Object.defineProperty(globalThis, k, { value: v, writable: true, configurable: true });
    }
});
