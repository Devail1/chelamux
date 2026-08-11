// CMX-230 — "the dashboard reads as a dev tool because of TYPE SCALE, NAV COUNT
// and HUE COUNT" (Liav's Xirp comparison, 2026-08-11). This is the ticket's own
// GUARDS list, each written to go RED under the specific corruption it names —
// same discipline as tests/kanban_lane_model.test.mjs: assert a precise value
// against the REAL source/module, not "some rule exists".
//
// Deliberate scoping note (read before extending this file): the ticket's
// accessibility ask names four abstract states — idle/working/waiting/error.
// They don't all live on one component. "error" specifically does NOT exist as
// an agent-liveness state in this codebase (chela/agent_manager.py's `liveness`
// docstring: "discovery only ever lists LIVE windows... a listed window is
// never dead" — there is no fourth agent status to fabricate one for). The
// three real state-pill families this file guards are: the wall's .gs-state
// pill (idle/working/needs-you/done/unknown — "needs-you" is this codebase's
// name for "waiting"), the sidebar's .ar-state line (idle/working/waiting), and
// the Work board's .kanban-state-chip/.kanban-card-error (failed is the real
// analogue of "error" — a task-level failure, not an agent one). All three
// already carried a non-hue cue before this ticket; what's new here is the
// guard, not the cue.
//
// Run: node --test tests/dashboard_scale_nav_a11y.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { tileState } from '../chela/dashboard/static/js/wallmodel.js';
import { primaryNavViews, secondaryNavViews } from '../chela/dashboard/static/js/viewreg.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'chela', 'dashboard');
const src = p => readFileSync(join(ROOT, p), 'utf8');

const CSS = src('static/style.css');
const TERMINALS = src('static/js/terminals.js');
const NAV = src('static/js/nav.js');
const KANBAN = src('static/js/kanban.js');
const VIEWS_SRC = src('static/js/views.js');

// ---------------------------------------------------------------------------
// A minimal, non-nested-aware CSS block splitter — good enough for this
// stylesheet (same honest scoping tests/wallnav.test.mjs's media-aware parser
// documents: jsdom can't resolve the cascade, so these are source-text facts).
// Strips comments first so a commented-out example never masquerades as a rule.
// ---------------------------------------------------------------------------
function cssBlocks(css) {
    const noComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const blocks = [];
    const re = /([^{}]+)\{([^{}]*)\}/g;
    let m;
    while ((m = re.exec(noComments))) {
        blocks.push({ selector: m[1].trim(), body: m[2] });
    }
    return blocks;
}
// The BASE (first-declared, top-level) rule body for `selector` — a selector
// like `.gs-head` can legitimately appear again inside a `@media (max-width:
// 768px)` override with its own SMALLER literal (tests/wallnav.test.mjs's
// CMX-133 test guards that pair's relationship); this file only cares about
// the base desktop rule that carries the token.
function blockFor(css, selector) {
    const found = cssBlocks(css).filter(b =>
        b.selector.split(',').map(s => s.trim()).includes(selector));
    assert.ok(found.length >= 1, `no CSS rule found for selector ${selector}`);
    return found[0].body;
}
function rootTokenPx(css, name) {
    const root = css.match(/:root\s*\{([^}]*)\}/);
    assert.ok(root, ':root block not found');
    const m = root[1].match(new RegExp('--' + name + ':\\s*([0-9.]+)px'));
    assert.ok(m, `--${name} not declared as a px value on :root`);
    return parseFloat(m[1]);
}

// --- GUARD 1: the type scale is TOKENISED — no bare font-size literal survives
// in the wall/pane/card rules (CMX-230 objective 1). Corrupting any ONE of
// these back to a bare `font-size: Npx` literal removes the single lever the
// ticket demanded and must fail here. .pane-recap::after (the chevron glyph)
// and .gs-idx (a fixed-box numeric badge, tightly coupled to --term-ctx-bar-h's
// own px math — see the big comment above .gs-idx in style.css) are
// deliberately EXCLUDED: neither is prose text, and gs-idx's box geometry is a
// separate, already-guarded lever (tests/wallnav.test.mjs's CMX-129 test).
const WALL_PANE_SELECTORS = ['.gs-head', '.pane-subtitle', '.pane-recap', '.gs-state', '.term-ctx-bar'];
const CARD_SELECTORS = ['.ar-title', '.ar-sub', '.ar-ctx'];

test('type scale: every wall/pane rule\'s font-size is a var() token, not a bare px literal', () => {
    for (const sel of WALL_PANE_SELECTORS) {
        const body = blockFor(CSS, sel);
        assert.match(body, /font-size:\s*var\(--wall-pane-font-size(-sm)?\)/,
            `${sel} must set font-size from a --wall-pane-font-size* token`);
        assert.doesNotMatch(body, /font-size:\s*[0-9.]+px/,
            `${sel} has reverted to a bare font-size px literal — the type scale has no single lever again`);
    }
});

test('type scale: every sidebar-card rule\'s font-size is the --card-font-size token, not a bare px literal', () => {
    for (const sel of CARD_SELECTORS) {
        const body = blockFor(CSS, sel);
        assert.match(body, /font-size:\s*var\(--card-font-size\)/,
            `${sel} must set font-size from --card-font-size`);
        assert.doesNotMatch(body, /font-size:\s*[0-9.]+px/,
            `${sel} has reverted to a bare font-size px literal`);
    }
});

// --- GUARD 2: minimum-legibility floor. Lowering the pane font-size or
// line-height token back toward the pre-CMX-230 values (10px / 1.3) must fail.
test('minimum legibility floor: --wall-pane-font-size and --wall-pane-line-height are above the pre-CMX-230 floor', () => {
    const fs = rootTokenPx(CSS, 'wall-pane-font-size');
    assert.ok(fs >= 11, `--wall-pane-font-size (${fs}px) has dropped back toward the old 10px pane text`);
    const root = CSS.match(/:root\s*\{([^}]*)\}/)[1];
    const lh = root.match(/--wall-pane-line-height:\s*([0-9.]+)/);
    assert.ok(lh, '--wall-pane-line-height not declared on :root');
    assert.ok(parseFloat(lh[1]) >= 1.45,
        `--wall-pane-line-height (${lh[1]}) has dropped back toward the old 1.3 leading`);
});

// --- GUARD 2z: GUARD 1 checks every wall/pane rule's font-size is a var() token;
// GUARD 2 floors --wall-pane-line-height's own :root declaration. Neither asserts
// that any rule actually CONSUMES the line-height token at its use site — a judge
// round reverted .pane-recap's line-height to the exact pre-CMX-230 literal (1.3)
// while leaving the :root token declared at 1.5 and .pane-recap's font-size still
// tokenised, and both guards stayed green. This pins the two selectors that ship
// prose text needing real leading (.pane-subtitle, .pane-recap) to read
// line-height FROM the token, not a bare literal.
const LINE_HEIGHT_SELECTORS = ['.pane-subtitle', '.pane-recap'];
test('type scale: .pane-subtitle and .pane-recap read line-height from --wall-pane-line-height, not a bare literal', () => {
    for (const sel of LINE_HEIGHT_SELECTORS) {
        const body = blockFor(CSS, sel);
        assert.match(body, /line-height:\s*var\(--wall-pane-line-height\)/,
            `${sel} must set line-height from var(--wall-pane-line-height)`);
        assert.doesNotMatch(body, /line-height:\s*[0-9.]+[^;]*;/,
            `${sel} has reverted to a bare line-height literal — the leading token has no effect at its use site`);
    }
});

// --- GUARD 2b: the legibility floor also covers the two tokens GUARD 2 does not
// reach. --card-font-size (the sidebar-card scale — the ticket's HEADLINE objective
// is raising 9-10px card text to 11.5px) and --wall-pane-font-size-sm (the .gs-state
// pill's own escape hatch, exempt from GUARD 2's >= 11px floor by construction) can
// both be shrunk back toward or below their pre-CMX-230 values while GUARD 1's
// "is it a var()" check and every other guard here stay green.
test('minimum legibility floor: --card-font-size and --wall-pane-font-size-sm are above the pre-CMX-230 floor', () => {
    const card = rootTokenPx(CSS, 'card-font-size');
    assert.ok(card >= 11, `--card-font-size (${card}px) has dropped back toward the old 9-10px card text`);
    const sm = rootTokenPx(CSS, 'wall-pane-font-size-sm');
    assert.ok(sm > 10, `--wall-pane-font-size-sm (${sm}px) has dropped back to (or below) the old 10px pill text`);
});

// --- GUARD 2c: "air in the chrome" at low densities (the CMX-230 comment above
// `body.wall-density-airy #term-stage` in style.css names THIS file's density
// guard by filename — this test is it). Two facts, both source-text since neither
// is behaviourally provable in jsdom the way GUARD 2's numeric tokens are:
//
//   1. _setWallDensity's own cutoff is pinned at `<= 2` — style.css's comment's
//      claim, made true.
//
//   2. buildWall calls _setWallDensity UNCONDITIONALLY (given a persisted
//      _wallPreset), not from behind dead code. This one is genuinely
//      unreachable-by-behaviour: buildWall only ever runs from inside
//      renderTerminals, which ALWAYS calls _refitWallForDock() (itself calling
//      the UNMUTATED applyGridLayout, which sets the SAME class) synchronously
//      moments later in the very same render pass — so disabling buildWall's own
//      restore line produces byte-identical DOM after every real render path a
//      test can drive, no matter which public entry point exercises it. Anchored
//      to the exact `if (_wallPreset) _setWallDensity(...)` form so a dead-code
//      wrap (`if (false && _wallPreset) ...`) — which a plain substring/regex
//      match on the call alone would miss, the exact hole GUARD 3a below fell
//      into — cannot pass.
test('density guard: _setWallDensity cuts off at <=2 panes, and buildWall restores it unconditionally on first paint', () => {
    const fn = TERMINALS.slice(TERMINALS.indexOf('function _setWallDensity'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    assert.match(body, /wall-density-airy['"],\s*cols\s*\*\s*rows\s*<=\s*2\)/,
        '_setWallDensity must toggle wall-density-airy off `cols * rows <= 2` — the style.css comment\'s claimed cutoff');

    const build = TERMINALS.slice(TERMINALS.indexOf('function buildWall'));
    const buildBody = build.slice(0, build.indexOf('\nfunction ', 10));
    assert.match(buildBody, /if\s*\(\s*_wallPreset\s*\)\s*_setWallDensity\(_wallPreset\.cols,\s*_wallPreset\.rows\);/,
        'buildWall must call _setWallDensity(_wallPreset.cols, _wallPreset.rows) unconditionally when _wallPreset ' +
        'is set — not from behind a dead `if (false && ...)` — so a reload sitting on a 1/2-col preset is airy on ' +
        'its own first paint, independent of applyGridLayout\'s later call');
});

// --- WIRING: the "air in the chrome" feature must actually PRODUCE air, not just
// toggle a class. GUARD 2c above pins that body.wall-density-airy gets TOGGLED at
// the right cutoff; that says nothing about what the class rule itself DOES. A
// judge round zeroed body.wall-density-airy #term-stage's horizontal padding to
// 0px while leaving the class-toggle logic and GUARD 2c's regexes untouched, and
// both density guards stayed green while the ticket's actual objective (wide
// margins at low density — Liav's Xirp comparison) silently regressed to
// edge-to-edge dense. jsdom can't resolve the cascade (this file's own note
// above cssBlocks), so this is a source-text floor on the same class GUARD 2c
// already pins, mirroring GUARD 2's numeric-floor discipline.
test('WIRING: the airy-density rule actually pads the stage — not just an empty class toggle', () => {
    const stageBody = blockFor(CSS, 'body.wall-density-airy #term-stage');
    const left = stageBody.match(/padding-left:\s*clamp\(([0-9.]+)px/);
    const right = stageBody.match(/padding-right:\s*clamp\(([0-9.]+)px/);
    assert.ok(left, 'body.wall-density-airy #term-stage has no padding-left: clamp(...) rule');
    assert.ok(right, 'body.wall-density-airy #term-stage has no padding-right: clamp(...) rule');
    assert.ok(parseFloat(left[1]) > 0,
        `body.wall-density-airy #term-stage's padding-left clamp floor (${left[1]}px) has been zeroed — the airy class toggles but produces no margin`);
    assert.ok(parseFloat(right[1]) > 0,
        `body.wall-density-airy #term-stage's padding-right clamp floor (${right[1]}px) has been zeroed — the airy class toggles but produces no margin`);

    const gridBody = blockFor(CSS, 'body.wall-density-airy #term-stage .grid-stack');
    const maxWidth = gridBody.match(/max-width:\s*([0-9.]+)px/);
    assert.ok(maxWidth, 'body.wall-density-airy #term-stage .grid-stack has no max-width rule');
    assert.ok(parseFloat(maxWidth[1]) > 0,
        `the centred max-width column (${maxWidth[1]}px) has been zeroed — panes would stretch edge-to-edge again`);
});

// --- GUARD 3: non-hue cue, per real state family — deleting the glyph/word
// span (or the text it carries) and leaving only the colour class must fail.

// 3a. The wall's .gs-state pill: tileState() (wallmodel.js, pure) is the ONE
// source both the initial paneHead markup and every live repaint
// (_applyWallTileFrame) draw from — see terminals.js below. Every state it can
// report must carry a non-empty glyph AND a non-empty word; blanking either
// for any one state (idle/working/needs-you/done/unknown) fails here.
test('non-hue cue — wall .gs-state pill: every tileState() result carries a glyph AND a word', () => {
    const cases = [
        { label: 'idle', agent: null, wants: false },
        { label: 'working', agent: { session_status: 'busy' }, wants: false },
        { label: 'needs-you (waiting)', agent: {}, wants: true },
        { label: 'done', agent: { session_status: 'idle', pr: { url: 'https://x' } }, wants: false },
        { label: 'unknown', agent: { claude_running: true, session_status: null }, wants: false },
    ];
    for (const c of cases) {
        const s = tileState(c.agent, c.wants);
        assert.ok(s.glyph && s.glyph.trim(), `${c.label}: tileState() glyph is empty — colour would be the only cue`);
        assert.ok(s.word && s.word.trim(), `${c.label}: tileState() word is empty — colour would be the only cue`);
    }
});

test('non-hue cue — wall .gs-state pill: both the initial paint AND every live repaint set the glyph + word text nodes', () => {
    // Initial paint: paneHead's own literal markup (terminals.js), before the
    // first live repaint ever runs.
    assert.match(TERMINALS, /gs-state-glyph[^>]*>[^<]*<\/span><span class="gs-state-word">idle<\/span>/,
        'paneHead\'s initial .gs-state markup no longer carries both a glyph and the word "idle"');
    // Live repaint: _applyWallTileFrame must write BOTH text nodes from
    // tileState()'s result, not just recolour the pill via className.
    const frame = TERMINALS.slice(TERMINALS.indexOf('function _applyWallTileFrame'));
    const body = frame.slice(0, frame.indexOf('\nfunction ', 10));
    assert.match(body, /el\.className\s*=\s*'gs-state gs-state-'\s*\+\s*s\.cls/,
        '_applyWallTileFrame must still recolour the pill from tileState().cls');
    assert.match(body, /g\.textContent\s*=\s*s\.glyph/,
        '_applyWallTileFrame no longer repaints the glyph text node — a live state change would go hue-only');
    assert.match(body, /w\.textContent\s*=\s*s\.word/,
        '_applyWallTileFrame no longer repaints the word text node — a live state change would go hue-only');
});

// 3b. The sidebar's .ar-state line: idle/working/waiting, each a real word
// (nav.js's _AGENT_STATUS_WORD), rendered as the span's TEXT content, not just
// its class.
test('non-hue cue — sidebar .ar-state: idle/working/waiting each map to a real word, not blank', () => {
    const m = NAV.match(/_AGENT_STATUS_WORD\s*=\s*\{([^}]*)\}/);
    assert.ok(m, '_AGENT_STATUS_WORD not found in nav.js');
    const map = Object.fromEntries(
        [...m[1].matchAll(/(\w+):\s*'([^']*)'/g)].map(mm => [mm[1], mm[2]]));
    for (const [color, want] of [['green', 'working'], ['yellow', 'waiting'], ['grey', 'idle']]) {
        assert.equal(map[color], want, `_AGENT_STATUS_WORD.${color} must be the word "${want}"`);
    }
});

test('non-hue cue — sidebar .ar-state: the rendered row interpolates the WORD into the element, not just a colour class', () => {
    assert.match(NAV, /<span class="ar-state \$\{stCls\}">\$\{stWord\}<\/span>/,
        '_agentRowHtml no longer renders stWord as the .ar-state text — a red-weak reader would see only the colour class');
});

// 3c. The Work board's closest real analogue of "error": a failed task. The
// WORD is what the ticket demands survive greyscale — kanban.js's STATUS_CHIPS
// already carries one per state; this pins 'failed' specifically, and that the
// card-level last_error message renders as real text, not colour-only.
test('non-hue cue — kanban "error" analogue (failed): the status chip carries a real word, not colour alone', () => {
    const m = KANBAN.match(/failed:\s*\{\s*label:\s*'([^']*)'/);
    assert.ok(m, 'STATUS_CHIPS.failed not found in kanban.js');
    assert.ok(m[1].trim().length > 0, 'STATUS_CHIPS.failed.label is empty — the failed chip would be colour-only');
    assert.match(m[1], /failed/i, 'STATUS_CHIPS.failed.label must literally say "failed"');
});

test('non-hue cue — kanban card error: the error TEXT renders on the card, not just .kanban-card-error\'s red colour', () => {
    assert.match(KANBAN, /class="kanban-card-error"[^>]*>\$\{escHtml\(card\.last_error/,
        'the card no longer renders card.last_error as text — only the red .kanban-card-error colour would remain');
});

// --- GUARD 4: a context-% threshold is never colour-only — the exact number
// (and, on the wall, the raw token count too) renders as text alongside the
// warn/danger class.
test('context-% threshold non-hue cue — sidebar .ar-ctx renders the numeric percentage as text, class is reinforcement only', () => {
    assert.match(NAV, /class="ar-ctx \$\{cls\}"[^>]*>\$\{p\}%<\/span>/,
        'the sidebar context chip no longer interpolates the numeric percentage as text');
});

test('context-% threshold non-hue cue — the wall .gs-ctx chip renders "N% · used/total" as text, class is reinforcement only', () => {
    const fn = TERMINALS.slice(TERMINALS.indexOf('function _applyTermContext'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    assert.match(body, /ctxChip\.textContent\s*=\s*`\$\{pct\}%/,
        '.gs-ctx no longer sets its textContent from the numeric pct — a threshold crossing would be colour-only');
    assert.match(body, /ctxChip\.className\s*=\s*'gs-ctx'\s*\+\s*\(sev/,
        '.gs-ctx no longer applies the warn/danger class as a SEPARATE reinforcement on top of the text');
});

// --- GUARD 5: footer completeness — model, spend, branch, context% and tokens
// (the exact 5 fields the ticket says "this ticket does NOT trade away") must
// all still exist in the wall pane footer. Removing any one field's chip must
// fail here, NAMING the missing field.
test('wall pane footer completeness: model + spend + branch + context% + tokens all present', () => {
    const fn = TERMINALS.slice(TERMINALS.indexOf('function _ctxBarHTML'));
    const body = fn.slice(0, fn.indexOf('\nfunction ', 10));
    const FIELDS = {
        'model': /class="gs-model"/,
        'spend (cost)': /class="gs-cost"/,
        'branch': /class="gs-branch"/,
        // context% AND tokens both render inside the SAME .gs-ctx chip — see
        // _applyTermContext's `${pct}%...${counter}` template, asserted above.
        'context% / tokens (gs-ctx)': /class="gs-ctx"/,
    };
    for (const [name, re] of Object.entries(FIELDS)) {
        assert.match(body, re, `the wall pane footer is missing its ${name} field`);
    }
});

// --- GUARD 6: single accent — every `.active` (the "you are here" / current-
// selection vocabulary) CSS rule may highlight with --accent and neutral
// tokens only (--text/--text-dim/--bg/--surface/--surface-2/--border, or the
// #0d1117 text-on-accent contrast colour). Adding a second accent-ish hue
// (another --ok-*/--green/--yellow/--red/--orange token, or a fresh hex) to
// any `.active` rule must fail here.
const NEUTRAL_VAR_RE = /--(text(-dim)?|bg|surface(-2)?|border|room-accent)\b/;
const NEUTRAL_HEX_RE = /#0d1117\b/;
test('single accent: every .active rule\'s highlight colour is --accent (or a neutral), never a second hue', () => {
    const activeBlocks = cssBlocks(CSS).filter(b =>
        b.selector.split(',').map(s => s.trim()).some(s => /\.active(::|\s|$|\.)/.test(s + ' ')));
    assert.ok(activeBlocks.length >= 5, 'too few .active rules found — did the selector scan break?');
    for (const b of activeBlocks) {
        const vars = [...b.body.matchAll(/var\(\s*--([\w-]+)/g)].map(m => '--' + m[1]);
        const hexes = [...b.body.matchAll(/#[0-9a-fA-F]{3,6}\b/g)].map(m => m[0]);
        for (const v of vars) {
            assert.ok(v === '--accent' || NEUTRAL_VAR_RE.test(v),
                `${b.selector} { ${b.body.trim().slice(0, 60)}... } references ${v} — a second accent hue, not --accent or a neutral`);
        }
        for (const h of hexes) {
            assert.ok(NEUTRAL_HEX_RE.test(h),
                `${b.selector} references a raw hex colour ${h} outside the neutral text-on-accent allowlist`);
        }
    }
});

// --- GUARD 7: nav inventory — the primary rail is EXACTLY 3 domain objects
// (Feed/Wall/Work); Knowledge/Agents/Personas/Cost are demoted, not deleted
// (still enabled, still reachable — see primaryNavViews/secondaryNavViews's
// shared navViews() base). Promoting any demoted view back to `tier: 'primary'`
// — or forgetting to demote a new one — must fail here.
function shippedViewEntries() {
    const body = VIEWS_SRC.split('export const VIEWS')[1];
    const ids = [...body.matchAll(/^\s+id:\s*'([^']+)'/gm)];
    return ids.map((m, i) => {
        const start = m.index;
        const end = i + 1 < ids.length ? ids[i + 1].index : body.length;
        const block = body.slice(start, end);
        const tierM = block.match(/tier:\s*'([^']+)'/);
        return { id: m[1], virtual: /virtual:\s*true/.test(block), tier: tierM ? tierM[1] : undefined };
    });
}

test('nav inventory: the shipped primary rail is exactly Feed, Wall, Work — nothing more, nothing less', () => {
    const entries = shippedViewEntries().filter(v => !v.virtual);
    assert.ok(entries.length >= 7, 'view extraction from views.js found too few entries — did its shape change?');
    const primary = primaryNavViews(entries, { terminalsOn: true }).map(v => v.id);
    assert.deepEqual(primary, ['feed', 'terminals', 'work'],
        'the primary nav set drifted from the ticket\'s exact 3 domain objects');
});

test('nav inventory: Knowledge/Agents/Personas/Cost are demoted (secondary), not deleted', () => {
    const entries = shippedViewEntries().filter(v => !v.virtual);
    const secondary = secondaryNavViews(entries, { terminalsOn: true }).map(v => v.id);
    assert.deepEqual(secondary, ['knowledge', 'agents', 'personas', 'cost'],
        'the demoted set no longer matches the ticket\'s "attributes of things, not places to go" list');
});

test('nav inventory: demoting a view never removes it from the sidebar entirely — it still renders somewhere', () => {
    // primaryNavViews + secondaryNavViews must partition the SAME navViews() base
    // navViews() itself already filters (virtual, enabled) — every remaining view
    // lands in exactly one of the two groups; neither drops it.
    const entries = shippedViewEntries().filter(v => !v.virtual);
    const ctx = { terminalsOn: true };
    const all = [...primaryNavViews(entries, ctx), ...secondaryNavViews(entries, ctx)].map(v => v.id).sort();
    const expected = entries.map(v => v.id).sort();
    assert.deepEqual(all, expected, 'a non-virtual, enabled view fell out of BOTH the primary and secondary nav groups');
});

// --- index.html carries the #side-nav-more container the demoted group renders
// into (nav.js's renderNav guards on it, so a missing container degrades
// silently to "those 4 views vanish from the sidebar" rather than a crash —
// this is the guard that actually catches that).
test('index.html declares #side-nav-more — the demoted group\'s render target', () => {
    const html = src('templates/index.html');
    assert.match(html, /id="side-nav-more"/, 'index.html no longer has a #side-nav-more container for the demoted nav group');
});
