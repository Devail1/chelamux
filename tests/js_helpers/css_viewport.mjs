// CMX-377 round 5 — resolve the conditional at-rules jsdom's cascade SKIPS.
//
// jsdom's getComputedStyle() resolves the cascade (specificity, then source
// order) for plain top-level style rules, but it never applies a rule nested in
// an `@supports`, `@media` or `@container` block — it parses them into the
// CSSOM and then ignores them. So a cascade-resolved guard mounted on the raw
// stylesheet is blind to ANY override wrapped in one: PR #529 round 4's judge
// re-created the off-screen-Wall bug with
// `@supports (display: grid) { .app > .canvas { grid-row: 2; } }` — always true
// in every real browser, invisible to jsdom (defeat_shapes #377d).
//
// `cssForViewport(css, viewport)` answers the question a browser answers at a
// given viewport: it walks the stylesheet's real CSSOM (parsed by jsdom itself,
// not a regex) and emits a FLAT stylesheet — every conditional block that
// matches the viewport unwrapped in place (source order preserved), every one
// that doesn't dropped. Mount the result instead of the raw CSS and the
// cascade-resolved guards see what a browser at that viewport sees.
//
// ⛔ It FAILS CLOSED: an at-rule or media feature it does not know how to
// evaluate throws, rather than being silently kept or dropped — a new wrapper
// shape must be taught here, never slip past every guard that mounts through it.
import { JSDOM } from 'jsdom';   // needs `npm ci` — tests/test_js_suites.py enforces it

export const DESKTOP = Object.freeze({ width: 1440, height: 900 });
export const PHONE = Object.freeze({ width: 390, height: 844 });
export const DESKTOP_DARK = Object.freeze({ ...DESKTOP, colorScheme: 'dark' });

function _px(v) {
    const m = String(v).trim().match(/^(-?\d+(?:\.\d+)?)(px|em|rem)?$/);
    if (!m) throw new Error(`cssForViewport: cannot evaluate length ${JSON.stringify(v)}`);
    return Number(m[1]) * (m[2] === 'em' || m[2] === 'rem' ? 16 : 1);
}

// One `(feature: value)` against the viewport. Unknown ⇒ throw.
function _feature(feat, vp) {
    const m = feat.match(/^\(\s*([a-z-]+)\s*(?::\s*([^)]+?))?\s*\)$/);
    if (!m) throw new Error(`cssForViewport: cannot evaluate media feature ${JSON.stringify(feat)}`);
    const [, name, value] = m;
    switch (name) {
        case 'min-width': return vp.width >= _px(value);
        case 'max-width': return vp.width <= _px(value);
        case 'min-height': return vp.height >= _px(value);
        case 'max-height': return vp.height <= _px(value);
        case 'orientation': return value === (vp.width >= vp.height ? 'landscape' : 'portrait');
        case 'prefers-reduced-motion': return value === (vp.reducedMotion ? 'reduce' : 'no-preference');
        case 'prefers-color-scheme': return value === (vp.colorScheme || 'light');
        case 'hover': return value === (vp.width > 768 ? 'hover' : 'none');
        case 'pointer': return value === (vp.width > 768 ? 'fine' : 'coarse');
        default: throw new Error(`cssForViewport: unknown media feature ${JSON.stringify(name)}`);
    }
}

// A media query list: comma = OR; each query = [not|only] [type] and (f) and (f)…
function _mediaMatches(text, vp) {
    return text.split(',').some(raw => {
        let q = raw.trim();
        let negate = false;
        if (/^not\s/i.test(q)) { negate = true; q = q.slice(4).trim(); }
        if (/^only\s/i.test(q)) q = q.slice(5).trim();
        const parts = q.split(/\s+and\s+/i).map(s => s.trim()).filter(Boolean);
        const ok = parts.every(p => {
            if (/^(all|screen)$/i.test(p)) return true;
            if (/^print$/i.test(p)) return false;
            return _feature(p, vp);
        });
        return negate ? !ok : ok;
    });
}

// @supports, evaluated the way the spec defines it (CMX-377 round 6): a real
// boolean grammar — `not C`, `C and C…`, `C or C…`, `( C )`, a `(prop: value)`
// declaration test and `selector(…)` — NOT a "does it start with `not`"
// shortcut. Round 5's shortcut treated every top-level `not (…)` as FALSE, so
// the judge hid the off-screen-Wall override inside
// `@supports not (display: chela-nonsense)` — TRUE in every real browser,
// dropped here (defeat_shapes #377e). A declaration is "supported" iff jsdom's
// own CSSOM accepts it (the same validator its parser uses: an unknown
// property or an invalid value is rejected); a selector iff querySelector
// parses it. Anything else throws (fail closed).
let _probeDoc;
function _declSupported(prop, value) {
    if (prop.startsWith('--')) return true;
    _probeDoc ??= new JSDOM('').window.document;
    const st = _probeDoc.createElement('div').style;
    st.setProperty(prop, value);
    return st.getPropertyValue(prop) !== '';
}
function _supportsMatches(text) {
    const src = String(text);
    let i = 0;
    const ws = () => { while (i < src.length && /\s/.test(src[i])) i++; };
    const word = w => {
        ws();
        const re = new RegExp(`^${w}(?=[\\s(])`, 'i');
        if (re.test(src.slice(i))) { i += w.length; return true; }
        return false;
    };
    const group = () => {   // returns the text between a balanced ( … ), cursor after `)`
        ws();
        if (src[i] !== '(') throw new Error(`cssForViewport: cannot evaluate @supports ${JSON.stringify(src)}`);
        let depth = 0; const start = i + 1;
        for (; i < src.length; i++) {
            if (src[i] === '(') depth++;
            else if (src[i] === ')' && --depth === 0) { i++; return src.slice(start, i - 1); }
        }
        throw new Error(`cssForViewport: unbalanced @supports ${JSON.stringify(src)}`);
    };
    const inParens = () => {
        ws();
        if (/^selector\(/i.test(src.slice(i))) {
            i += 'selector'.length;
            const sel = group();
            try { (_probeDoc ??= new JSDOM('').window.document).querySelector(sel); return true; } catch { return false; }
        }
        const inner = group();
        const decl = inner.match(/^\s*(--[\w-]+|[a-zA-Z-]+)\s*:\s*([\s\S]+?)\s*$/);
        if (decl && !/^\s*(not\b|\()/i.test(inner)) return _declSupported(decl[1].toLowerCase(), decl[2]);
        return _supportsMatches(inner);   // nested ( condition )
    };
    const condition = () => {
        if (word('not')) return !inParens();
        let v = inParens();
        for (;;) {
            if (word('and')) v = inParens() && v;
            else if (word('or')) v = inParens() || v;
            else return v;
        }
    };
    const v = condition();
    ws();
    if (i !== src.length) throw new Error(`cssForViewport: cannot evaluate @supports ${JSON.stringify(src)}`);
    return v;
}

// @container: evaluated against the viewport width (a full-width pane is the
// widest a container can be) unless the caller passes `containerWidth`.
function _containerMatches(text, vp) {
    const cond = text.replace(/^[a-zA-Z_][\w-]*\s+(?=\()/, '');   // drop an optional container name
    return _mediaMatches(cond, { ...vp, width: vp.containerWidth ?? vp.width });
}

function _flatten(rules, vp, out) {
    for (const r of rules) {
        const kind = r.constructor.name;
        switch (kind) {
            case 'CSSStyleRule':
                out.push(_longhandRule(r));
                break;
            case 'CSSFontFaceRule':
            case 'CSSKeyframesRule':
            case 'CSSImportRule':
            case 'CSSNamespaceRule':
            case 'CSSLayerStatementRule':
                out.push(r.cssText);
                break;
            case 'CSSMediaRule':
                if (_mediaMatches(r.media.mediaText, vp)) _flatten(r.cssRules, vp, out);
                break;
            case 'CSSSupportsRule':
                if (_supportsMatches(r.conditionText)) _flatten(r.cssRules, vp, out);
                break;
            case 'CSSContainerRule':
                if (_containerMatches(r.conditionText ?? r.containerQuery ?? '', vp)) _flatten(r.cssRules, vp, out);
                break;
            case 'CSSLayerBlockRule':
                // Unwrapping promotes layered rules above unlayered ones — an
                // over-approximation that can only turn a guard RED, never hide
                // an override from it.
                _flatten(r.cssRules, vp, out);
                break;
            default:
                throw new Error(`cssForViewport: unknown at-rule ${kind} — teach tests/js_helpers/css_viewport.mjs ` +
                    'how to evaluate it rather than letting it slip past every guard that mounts through here');
        }
    }
    return out;
}

// --- Shorthand → longhand normalisation (CMX-377 round 6, defeat_shapes #377e).
// jsdom's cascade stores each declaration under the property NAME it was
// written with and never folds a longhand into a shorthand (or expands the
// shorthands its CSSOM cannot parse — grid-row/-column/-area, and ANY
// shorthand carrying a var()). So `.canvas { grid-row: 1 }` +
// `.app > .canvas { grid-row-start: 2 }` read back `gridRow: "1"` although
// every browser puts it on row 2, and `.gs-head .x.idle { background:
// var(--c) }` never reaches `backgroundColor`. Every style rule is therefore
// re-emitted with these shorthands expanded to their longhands — guards read
// the LONGHANDS, which the cascade then resolves exactly like a browser.
const _SPLIT_WS = v => { const out = []; let depth = 0, cur = '';
    for (const ch of v) {
        if (ch === '(') depth++; else if (ch === ')') depth--;
        if (/\s/.test(ch) && depth === 0) { if (cur) out.push(cur); cur = ''; } else cur += ch;
    }
    if (cur) out.push(cur); return out; };
const _SPLIT = (v, sep) => { const out = []; let depth = 0, cur = '';
    for (const ch of v) {
        if (ch === '(') depth++; else if (ch === ')') depth--;
        if (ch === sep && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    out.push(cur.trim()); return out; };
const _SIDES = ['top', 'right', 'bottom', 'left'];
const _box = vals => [vals[0], vals[1] ?? vals[0], vals[2] ?? vals[0], vals[3] ?? vals[1] ?? vals[0]];
const _isIdent = t => /^[a-zA-Z_-][\w-]*$/.test(t) && !/^(auto|span|inherit|initial|unset|revert)$/i.test(t);
function _gridLine(v) { const [a, b] = _SPLIT(v, '/'); return [a, b ?? (_isIdent(a) ? a : 'auto')]; }
const _BORDER_STYLES = /^(none|hidden|dotted|dashed|solid|double|groove|ridge|inset|outset)$/i;
const _isWidth = t => /^(thin|medium|thick|0|-?[\d.]+[a-z%]+)$/i.test(t) || /^calc\(/i.test(t);
function _border(v) {
    let width = 'medium', style = 'none', color = 'currentcolor';
    for (const t of _SPLIT_WS(v)) {
        if (_BORDER_STYLES.test(t)) style = t; else if (_isWidth(t)) width = t; else color = t;
    }
    return { width, style, color };
}
const _BG_NON_COLOR = /^(repeat|repeat-x|repeat-y|no-repeat|space|round|scroll|fixed|local|top|bottom|left|right|center|cover|contain|auto|border-box|padding-box|content-box|text|\/|[\d.]+[a-z%]*)$/i;
const _isImage = t => /^(none|url\(|[a-z-]*gradient\(|image\(|image-set\(|cross-fade\()/i.test(t);
function _background(v) {
    const layers = _SPLIT(v, ',');
    const images = layers.map(l => _SPLIT_WS(l).find(_isImage) ?? 'none');
    const last = _SPLIT_WS(layers.at(-1)).filter(t => !_isImage(t) && !_BG_NON_COLOR.test(t));
    return { 'background-image': images.join(', '), 'background-color': last.at(-1) ?? 'transparent' };
}
function _font(v) {
    const toks = _SPLIT_WS(v);
    const i = toks.findIndex(t => /^([\d.]+[a-z%]+|xx?-small|x{0,3}-?large|small|medium|larger|smaller)(\/.*)?$/i.test(t));
    return { 'font-family': i < 0 ? v : v.slice(v.indexOf(toks[i]) + toks[i].length).trim() };
}
function _expand(prop, v) {
    switch (prop) {
        case 'grid-row': { const [s, e] = _gridLine(v); return { 'grid-row-start': s, 'grid-row-end': e }; }
        case 'grid-column': { const [s, e] = _gridLine(v); return { 'grid-column-start': s, 'grid-column-end': e }; }
        case 'grid-area': {
            const p = _SPLIT(v, '/');
            const d = i => p[i] ?? (_isIdent(p[0]) ? p[i === 3 ? 1 : 0] ?? p[0] : 'auto');
            return { 'grid-row-start': p[0], 'grid-column-start': d(1), 'grid-row-end': d(2), 'grid-column-end': d(3) };
        }
        case 'border-radius': {
            const [h, vv] = _SPLIT(v, '/').map(x => _box(_SPLIT_WS(x)));
            const corners = ['top-left', 'top-right', 'bottom-right', 'bottom-left'];
            return Object.fromEntries(corners.map((c, k) => [`border-${c}-radius`, vv && vv[k] !== h[k] ? `${h[k]} ${vv[k]}` : h[k]]));
        }
        case 'padding': case 'margin': {
            const b = _box(_SPLIT_WS(v));
            return Object.fromEntries(_SIDES.map((s, k) => [`${prop}-${s}`, b[k]]));
        }
        case 'border-width': case 'border-style': case 'border-color': {
            const b = _box(_SPLIT_WS(v)); const part = prop.slice(7);
            return Object.fromEntries(_SIDES.map((s, k) => [`border-${s}-${part}`, b[k]]));
        }
        case 'border': case 'border-top': case 'border-right': case 'border-bottom': case 'border-left': {
            const { width, style, color } = _border(v);
            const sides = prop === 'border' ? _SIDES : [prop.slice(7)];
            return Object.fromEntries(sides.flatMap(s => [
                [`border-${s}-width`, width], [`border-${s}-style`, style], [`border-${s}-color`, color]]));
        }
        case 'background': return _background(v);
        case 'font': return _font(v);
        default: return null;
    }
}
function _longhandRule(r) {
    const st = r.style;
    const present = new Set();
    for (let k = 0; k < st.length; k++) present.add(st.item(k));
    const decls = [];
    for (let k = 0; k < st.length; k++) {
        const prop = st.item(k);
        const value = st.getPropertyValue(prop);
        const bang = st.getPropertyPriority(prop) ? ' !important' : '';
        const longs = /\bvar\(/i.test(value) || /^(grid-row|grid-column|grid-area)$/.test(prop) ? _expand(prop, value) : null;
        if (!longs) { decls.push(`${prop}: ${value}${bang}`); continue; }
        // A longhand jsdom already expanded (present in its own right) wins —
        // it is the later, more exact declaration of the same rule.
        for (const [lp, lv] of Object.entries(longs)) if (!present.has(lp)) decls.push(`${lp}: ${lv}${bang}`);
    }
    return `${r.selectorText} { ${decls.join('; ')} }`;
}

/** Resolve every `var(--x[, fallback])` in `value` against `el`'s computed custom properties. */
export function resolveVars(win, el, value, depth = 0) {
    if (depth > 20) throw new Error(`resolveVars: var() cycle in ${JSON.stringify(value)}`);
    const cs = win.getComputedStyle(el);
    const out = String(value).replace(/var\(\s*(--[\w-]+)\s*(?:,\s*((?:[^()]|\([^()]*\))*))?\)/g,
        (_, name, fb) => { const v = cs.getPropertyValue(name).trim(); return v || (fb ?? '').trim(); });
    return /var\(/.test(out) ? resolveVars(win, el, out, depth + 1) : out.trim();
}

// The properties that make a status mark's SILHOUETTE (CMX-377 round 6): its
// clip, its four corner radii, whether it is FILLED (background) and whether
// it is OUTLINED (border width/style per side). Colour is deliberately absent
// — the brief's guard is "assert shape, not colour" — but a fill/no-fill flip
// is shape (idle's hollow ring vs working's solid dot), so background-color is
// reduced to transparent-or-not.
const _SHAPE_PROPS = ['clip-path', 'mask-image',
    ...['top-left', 'top-right', 'bottom-right', 'bottom-left'].map(c => `border-${c}-radius`),
    ..._SIDES.flatMap(s => [`border-${s}-width`, `border-${s}-style`]), 'background-image'];
const _isTransparent = v => /^(transparent|rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)|none|)$/i.test(v.trim());
export function shapeSignature(win, el) {
    const cs = win.getComputedStyle(el);
    const sig = {};
    for (const p of _SHAPE_PROPS) sig[p] = resolveVars(win, el, cs.getPropertyValue(p));
    const bg = resolveVars(win, el, cs.getPropertyValue('background-color'));
    sig.filled = !_isTransparent(bg);
    return sig;
}

/** Flatten `css` to what a browser at `viewport` applies, shorthands expanded to longhands. */
export function cssForViewport(css, viewport = DESKTOP) {
    const dom = new JSDOM('<!doctype html><html><head><style></style></head></html>');
    const style = dom.window.document.querySelector('style');
    style.textContent = css;
    return _flatten(style.sheet.cssRules, viewport, []).join('\n');
}
