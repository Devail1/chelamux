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

// @supports: every real browser this dashboard targets supports the modern
// properties/selectors the stylesheet can name, so a positive condition is TRUE
// (exactly the judge's round-4 case). A top-level `not (...)` is FALSE.
function _supportsMatches(text) {
    return !/^\s*not\b/i.test(text);
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

/** Flatten `css` to what a browser at `viewport` applies. */
export function cssForViewport(css, viewport = DESKTOP) {
    const dom = new JSDOM('<!doctype html><html><head><style></style></head></html>');
    const style = dom.window.document.querySelector('style');
    style.textContent = css;
    return _flatten(style.sheet.cssRules, viewport, []).join('\n');
}
