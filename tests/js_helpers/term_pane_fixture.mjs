// CMX-169's shared fixture: the real dashboard booted in jsdom at phone width, in single
// mode, over three sessions whose /api/agents order (@201, @202, @203) is never the
// sidebar order a test saves. One boot per test file (the module graph loads once).
import { APP_BODY, NAV, SIDEBAR, bootDashboardDom, flush, WALL } from './dashboard_dom.mjs';
import { fakeArchiveServer } from './fake_archive.mjs';

export const KEY = 'pc_term_last_wid';
export const ROWS = ['a', 'b', 'c'].map((n, i) => ({
    name: n, window_id: `@20${i + 1}`, session_id: `s-${n}`, online: true, claude_running: true,
    session_status: 'idle', cwd: `/srv/code/${['alpha', 'beta', 'gamma'][i]}`,
}));
export const stored = (wid, session_id, name) => JSON.stringify({ wid, session_id, name });


export async function settle() { for (let i = 0; i < 8; i++) await flush(); }
let wall = null;   // the booted page's features/wall
// The pane on screen: its frame (a pending placeholder in jsdom) names the wid.
export const shown = () => {
    const f = document.querySelector('#term-stage .term-pane .term-frame');
    return f ? f.dataset.pending || f.getAttribute('src') : null;
};
export const pane = () => ({ value: wall.activePane(), shown: shown() });

// `order`: the sidebar's saved group order. `seed`: localStorage before boot. `layoutGate`:
// a promise the sidebar's layout read waits on, holding the sidebar undrawn until it resolves.
export async function bootPhone({ order, seed = {}, layoutGate = null }) {
    const server = fakeArchiveServer(() => ROWS);
    server.handle('/api/sidebar/layout', { method: 'POST', body: JSON.stringify({ order }) });
    const json = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    const fetchImpl = (url, opts) => {
        const u = String(url);
        const faked = server.handle(url, opts);
        const body = faked !== undefined ? faked
            : /\/api\/agents$/.test(u) ? ROWS
            : /\/api\/agents\/context/.test(u) ? []
            : /\/api\/summary/.test(u) ? [] : {};
        return layoutGate && /\/api\/sidebar\/layout/.test(u) ? layoutGate.then(() => json(body)) : json(body);
    };
    const booted = await bootDashboardDom({
        body: APP_BODY, phone: true, canvasStub: true, fetchImpl,
        seedLocalStorage: { pc_term_mode: 'single', ...seed },
        extraModules: [NAV, WALL, SIDEBAR],
    });
    globalThis.fetch = fetchImpl;
    wall = booted.modules.terminals;
    return { nav: booted.modules.nav, sidebar: booted.modules.sidebar, terminals: booted.modules.terminals };
}
