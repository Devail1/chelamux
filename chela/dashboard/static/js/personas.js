// ---------------------------------------------------------------------------
// PERSONAS — the read-only view of chela's declared persona layer.
//
// `docs/PERSONA_PATTERN.md` describes three control-plane personas (judge, critic,
// orchestrator) as one idea, three times: mechanical facts in code, judgment in the
// LLM. The single source of truth for "what personas exist" is the Python registry
// (chela/personas/__init__.py), surfaced at /api/personas. This module renders it —
// one card per persona, with its trigger / mode / action-surface and a cheap live
// status where one is available (the judge is "reviewing cmx-N" while a run's
// judge_state=running). It DECLARES; it never launches or drives a persona.
//
// The render is split so the .mjs view test can drive the REAL DOM path: renderPersonas()
// builds the card list from a payload (pure over its input — three personas in, three
// cards out), and refreshPersonas() is the fetch+render the view's enter() hook calls.
// ---------------------------------------------------------------------------
import { $, api, escHtml } from './util.js';

// The registry order is the pipeline order (review code → review brief → run the loop);
// preserve it, and don't reorder client-side.
function personaCardHtml(p) {
    const title = escHtml(p.title || p.key || '');
    // The status pill: a live note (judge "reviewing cmx-N") wins; otherwise the
    // declared enabled/dormant state. Dormant is not an error — the orchestrator is
    // embedded-but-not-launched by design (CMX-90 launches it).
    let stateCls, stateText;
    if (p.status) {
        stateCls = 'on';
        stateText = p.status;
    } else if (p.enabled) {
        stateCls = 'on';
        stateText = 'enabled';
    } else {
        stateCls = 'dormant';
        stateText = 'dormant';
    }
    const rows = [
        ['Trigger', p.trigger],
        ['Mode', p.mode],
        ['Action surface', p.action_surface],
        ['Prompt', p.prompt_source],
    ].map(([k, v]) => `
        <div class="persona-row">
            <span class="persona-row-key">${escHtml(k)}</span>
            <span class="persona-row-val">${escHtml(v || '—')}</span>
        </div>`).join('');
    const docs = (p.docs || []).map(d =>
        `<code class="persona-doc">${escHtml(d)}</code>`).join(' ');
    return `
    <div class="persona-card" data-persona="${escHtml(p.key || '')}">
        <div class="persona-head">
            <span class="persona-title">${title}</span>
            <span class="persona-state ${stateCls}">${escHtml(stateText)}</span>
        </div>
        <div class="persona-summary">${escHtml(p.summary || '')}</div>
        <div class="persona-rows">${rows}</div>
        ${docs ? `<div class="persona-docs">${docs}</div>` : ''}
    </div>`;
}

// Pure over its input: render whatever personas the payload carries into #personas-list.
// One card per persona — drop one from the payload (or the registry that feeds it) and
// one card disappears, which is exactly what the view test asserts.
function renderPersonas(personas) {
    const host = $('#personas-list');
    if (!host) return;
    const list = personas || [];
    if (!list.length) {
        host.innerHTML = '<div class="side-empty">No personas declared</div>';
        return;
    }
    host.innerHTML = list.map(personaCardHtml).join('');
}

async function refreshPersonas() {
    let data;
    try {
        data = await api('/api/personas');
    } catch (e) {
        console.error('refreshPersonas', e);
        return;
    }
    renderPersonas((data && data.personas) || []);
}

// --- Stage 0: ES-module exports ---
export { personaCardHtml, refreshPersonas, renderPersonas };
