// Resolve templates/index.html's Jinja conditionals in a JS test, the way Flask's
// render_template would for a given config. Shared by js_helpers/dashboard_dom.mjs
// (sliceTemplate(…, ctx)) and browser/fixture.mjs (renderShell). Dependency-free.

/** Resolve index.html's Jinja `{% if [not] NAME %}…{% else %}…{% endif %}`
 * blocks against `ctx` (CMX-89: panel-work's `active` class is now one), so a
 * rendered fixture is what Flask serves for that config. The
 * template uses only that shape; any other `{% … %}` tag, or a NAME missing
 * from ctx, THROWS rather than being guessed at. `{{ … }}` is left as-is. */
export function renderTemplateIfs(html, ctx) {
    const out = [];
    const stack = [];   // per open {% if %}: { cond, inElse }
    const live = () => stack.every(f => (f.inElse ? !f.cond : f.cond));
    const tag = /\{%-?\s*(.*?)\s*-?%\}/g;
    let last = 0;
    for (let m; (m = tag.exec(html));) {
        if (live()) out.push(html.slice(last, m.index));
        last = tag.lastIndex;
        const stmt = m[1];
        const ifm = stmt.match(/^if\s+(not\s+)?([A-Za-z_]\w*)$/);
        if (ifm) {
            if (!(ifm[2] in ctx)) throw new Error(`renderTemplateIfs: ${ifm[2]} missing from ctx`);
            stack.push({ cond: Boolean(ctx[ifm[2]]) !== Boolean(ifm[1]), inElse: false });
        } else if (stmt === 'else' && stack.length) {
            stack[stack.length - 1].inElse = true;
        } else if (stmt === 'endif' && stack.length) {
            stack.pop();
        } else {
            throw new Error(`renderTemplateIfs: unsupported tag {% ${stmt} %}`);
        }
    }
    if (stack.length) throw new Error('renderTemplateIfs: unclosed {% if %}');
    out.push(html.slice(last));
    return out.join('');
}
