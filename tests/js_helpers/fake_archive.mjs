// A stand-in for the dashboard's sidebar-archive routes (CMX-75, chela/sidebar_archive.py)
// for the jsdom sidebar suites. It keeps the archive the way the server does — hidden keys
// and CLOSED-session records — so a suite drives the REAL nav.js against a real-shaped
// answer and reads back both the DOM and every request nav.js made.
//
//   const server = fakeArchiveServer(() => util._agentsCache);
//   fetchImpl: (url, opts) => server.handle(url, opts) ?? <the suite's own answer>
//
// Archive of `w:@N` CLOSES the window (it leaves `agents()` answers) unless the window
// carries `no_session: true` — the server's "session id cannot be determined" refusal.
// A `run:` key is only hidden. Unarchive of an `s:` key RESUMES it: a new window, same
// name + cwd, which `/api/agents` answers with from then on.

export function fakeArchiveServer(agents) {
    const st = { hidden: [], sessions: [] };
    const calls = [];
    const closed = new Set();
    const resumedWins = [];
    let nextWid = 9000;
    const state = () => JSON.parse(JSON.stringify(st));

    function post(path, body) {
        const keys = (body && body.keys) || [];
        if (path === '/api/sidebar/archive') {
            const archived = [], closedNow = [], refused = [];
            for (const k of keys) {
                if (k.startsWith('run:')) {
                    if (!st.hidden.includes(k)) st.hidden.push(k);
                    archived.push(k);
                    continue;
                }
                const wid = k.slice(2);
                const a = (agents() || []).find(x => x && x.window_id === wid);
                if (!a || a.no_session) {
                    refused.push({ key: k, error: `${wid}: its Claude session id cannot be determined` });
                    continue;
                }
                const sid = `sid-${wid.slice(1)}`;
                st.sessions.push({ key: `s:${sid}`, session_id: sid, name: a.name, cwd: a.cwd || '',
                    wid, archived_at: Date.now() / 1000 });
                closed.add(wid);
                archived.push(`s:${sid}`);
                closedNow.push(wid);
            }
            return { ok: !refused.length, archived, closed: closedNow, refused, state: state() };
        }
        if (path === '/api/sidebar/unarchive') {
            const resumed = [];
            st.hidden = st.hidden.filter(k => !keys.includes(k));
            for (const k of keys.filter(x => x.startsWith('s:'))) {
                const rec = st.sessions.find(r => r.key === k);
                if (!rec) continue;
                st.sessions = st.sessions.filter(r => r.key !== k);
                const win = { name: rec.name, window_id: `@${nextWid++}`, online: true, claude_running: true,
                    session_status: 'idle', cwd: rec.cwd, resumed_session: rec.session_id };
                resumedWins.push(win);
                resumed.push({ key: k, wid: win.window_id, name: win.name });
            }
            return { ok: true, resumed, refused: [], state: state() };
        }
        if (path === '/api/sidebar/archive/migrate') {
            for (const k of keys) if (!k.startsWith('s:') && !st.hidden.includes(k)) st.hidden.push(k);
            return { ok: true, state: state() };
        }
        return undefined;
    }

    return {
        st, calls, resumedWins,
        reset() { st.hidden = []; st.sessions = []; calls.length = 0; closed.clear(); resumedWins.length = 0; },
        // the live window list as the server would answer it: closed windows gone, resumed ones in
        liveAgents() { return [...(agents() || []).filter(a => a && !closed.has(a.window_id)), ...resumedWins]; },
        handle(url, opts) {
            const path = String(url).replace(/^https?:\/\/[^/]+/, '').replace(/\?.*$/, '');
            const method = (opts && opts.method) || 'GET';
            if (!path.startsWith('/api/sidebar/') && path !== '/api/agents') return undefined;
            const body = opts && opts.body ? JSON.parse(opts.body) : null;
            calls.push({ method, path, body });
            if (path === '/api/agents') return this.liveAgents();
            if (method === 'GET' && path === '/api/sidebar/archive') return state();
            return post(path, body);
        },
    };
}
