// CMX-95: what happens after Settings → Update (nav.js) POSTs /api/update/apply. A
// `started` reply is followed by a bounded poll of /api/update/status, so a click always
// ends in an outcome ("Restarted …", the error, a reload, or "couldn't confirm"), never an
// endless spinner.
import { api } from './util.js';

const POLL_MS = 2000;
// How long a page follows one click; past it, "couldn't confirm" — unknown is never success.
const FOLLOW_TIMEOUT_MS = 180000;

function _doneText(r) {
    const restarted = (r.restarted || []).slice().sort();
    const what = restarted.length
        ? `Restarted ${restarted.join(', ')}`
        : 'Done — no running service needed a restart';
    return r.behind_before > 0 ? `Pulled ${r.behind_before} commit(s). ${what}` : what;
}

// One step of the post-click state machine: polling → done | failed | timeout, or reload
// when THIS dashboard restarted (`boot` changed: nothing is left to report the outcome).
// `st` = latest /api/update/status (null if unreachable); `ctx` = {run, boot, label, startedAt}.
function updateOutcome(st, ctx, now) {
    if (st && st.boot && ctx.boot && st.boot !== ctx.boot) {
        return { state: 'reload', text: 'The dashboard restarted — reloading…' };
    }
    const r = st && st.last_result;
    if (st && st.applying === false && r && r.run === ctx.run) {
        if (r.ok === true) return { state: 'done', text: _doneText(r) };
        return { state: 'failed', text: `Update failed at ${r.step}: ${r.error || 'no detail'}` };
    }
    if (now - ctx.startedAt >= FOLLOW_TIMEOUT_MS) {
        return { state: 'timeout', text: "Couldn't confirm the restart — check `pm2 ls`." };
    }
    return { state: 'polling', text: `${ctx.label}…` };
}

// Follow a `started` /api/update/apply response to its outcome, calling onStep(step) on
// every poll. Resolves with the final step. Every dependency is injectable for the tests.
async function followUpdate(resp, onStep, {
    fetchStatus = () => api('/api/update/status'),
    sleep = ms => new Promise(r => setTimeout(r, ms)),
    now = () => Date.now(),
    reload = () => window.location.reload(),
} = {}) {
    const what = resp.detail || 'restarting';
    const ctx = {
        run: resp.run, boot: resp.boot, startedAt: now(),
        label: what.charAt(0).toUpperCase() + what.slice(1),
    };
    for (;;) {
        await sleep(POLL_MS);
        let st = null;
        try { st = await fetchStatus(); } catch (_) { st = null; }   // down mid-restart
        const step = updateOutcome(st, ctx, now());
        onStep(step);
        if (step.state === 'reload') { reload(); return step; }
        if (step.state !== 'polling') return step;
    }
}

export { FOLLOW_TIMEOUT_MS, POLL_MS, followUpdate, updateOutcome };
