// CMX-221: Settings > General, ported from tests/settings_update.test.mjs (the Update card,
// CMX-199 / CMX-56 / CMX-95), settings_remote_control, settings_file_drop and
// settings_telegram_topics (CMX-204), plus the agent mode and model and the projects folder.
import { act, fireEvent, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { updateDeps } from '../index.ts';
import { calls, must, openOn, pick, posts, routes, settle, type } from './setup.tsx';

// --- the Update card -----------------------------------------------------------------------

const btn = () => document.getElementById('update-apply-btn') as HTMLButtonElement | null;
const row = () => must(document.getElementById('update-status-row')).textContent?.replace(/\s+/g, ' ') ?? '';
const msg = () => must(document.getElementById('update-apply-msg'));
const BOOT = 'boot-1';
const status = (extra: object) => ({ ok: true, behind: 0, branch: 'dev', stale_ok: true, stale_services: [], boot: BOOT, applying: false, last_result: null, ...extra });

// A FAKE /api/update/status, one scripted entry per poll (an Error = unreachable), on a fake
// clock that advances per sleep, so the 180 s bound costs no real time.
let polls = 0, clock = 0, reloads = 0;
function useStatuses(list: unknown[]) {
  polls = clock = reloads = 0;
  Object.assign(updateDeps, {
    sleep: async (ms: number) => { clock += ms; },
    now: () => clock,
    reload: () => { reloads++; },
    fetchStatus: async () => {
      const v = list.length ? list[Math.min(polls++, list.length - 1)] : null;
      if (v instanceof Error) throw v;
      return v;
    },
  });
}
beforeEach(() => useStatuses([]));

async function openWith(update: object) {
  routes['/api/settings'] = { sections: [], update };
  await openOn();
}
async function click(confirmed = true) {
  vi.spyOn(window, 'confirm').mockReturnValue(confirmed);
  fireEvent.click(must(btn(), 'the Update button'));
  await settle(4);
}

test('a checkout that is behind offers Update now and says the count and the branch', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  expect(btn()?.disabled).toBe(false);
  expect(btn()?.textContent).toBe('Update now');
  expect(row()).toMatch(/3 behind/);
  expect(row()).toMatch(/branch dev: 3 commit\(s\) unpulled/);
});

test('up to date offers no button at all', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: [], stale_ok: true });
  expect(btn()).toBeNull();
  expect(row()).toMatch(/Up to date/);
  expect(must(document.querySelector('#update-status-row .s-status-badge')).className).toMatch(/\bon\b/);
});

test('an unreadable service-freshness check is Unknown, never "Up to date", and offers nothing', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: [], stale_ok: false });
  expect(row()).not.toMatch(/Up to date/);
  expect(row()).toMatch(/Unknown.*could not read which services run old code/);
  expect(btn()).toBeNull();
});

test('an unreadable checkout reports Unknown with its reason, and no button', async () => {
  await openWith({ ok: false, git: false, error: 'not a git checkout' });
  expect(row()).toMatch(/Unknown\s*not a git checkout/);
  expect(btn()).toBeNull();
});

test('a failed /api/settings read renders Unknown (and the status list says so), not a stale state', async () => {
  routes['/api/settings'] = new Error('network down');
  await openOn();
  expect(row()).toMatch(/Unknown/);
  expect(btn()).toBeNull();
  expect(must(document.querySelector('#settings-status')).textContent).toMatch(/Status unavailable\./);
});

test('nothing to pull but stale services: never "Up to date", names them, offers the restart', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-daemon', 'chela-dashboard'] });
  expect(row()).not.toMatch(/Up to date/);
  expect(btn()?.textContent).toBe('Restart stale services (2)');
  expect(btn()?.disabled).toBe(false);
});

test.each([
  [['chela-daemon', 'chela-dashboard'], 'branch dev: nothing to pull, but code that chela-daemon, chela-dashboard run changed after they started (or pm2 restart chela-daemon chela-dashboard)'],
  [['chela-daemon'], 'branch dev: nothing to pull, but code that chela-daemon runs changed after it started (or pm2 restart chela-daemon)'],
])('the stale sentence agrees with its count (%j)', async (stale, text) => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: stale });
  expect(must(document.querySelector('#update-status-row .s-status-detail')).textContent?.replace(/\s+/g, ' ')).toBe(text);
  // the command is its own block, one unit per argument (CMX-57)
  const code = must(document.querySelector('#update-status-row code.s-cmd'));
  expect([...code.querySelectorAll('.s-cmd-arg')].map((a) => a.textContent)).toEqual(stale);
});

test('a service whose freshness is unknown is named as such, on every readable state', async () => {
  for (const extra of [{ behind: 2 }, { stale_services: ['chela-daemon'] }, {}]) {
    routes['/api/settings'] = { sections: [], update: { ok: true, behind: 0, branch: 'dev', stale_services: [], unknown_services: ['chela-agent-terminals'], ...extra } };
    await openOn();
    expect(row()).toMatch(/freshness unknown for chela-agent-terminals/);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Close"]')?.click());
    await settle(1);
  }
});

test('Restart says what it is restarting, follows the poll, then states the outcome', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-collab', 'chela-daemon', 'chela-telegram'] });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 4, boot: BOOT, detail: 'restarting chela-collab, chela-daemon, chela-telegram' };
  const seen: string[] = [];
  useStatuses([status({ applying: true }), status({ applying: true }),
    status({ last_result: { run: 4, ok: true, step: 'done', error: '', behind_before: 0, restarted: ['chela-telegram', 'chela-collab', 'chela-daemon'] } })]);
  const fetchStatus = updateDeps.fetchStatus;
  // what the card says while each poll is out (after React drew it: the fake sleep takes no time)
  updateDeps.fetchStatus = async () => {
    await new Promise((r) => setTimeout(r, 0));
    seen.push(`${msg().className}|${msg().textContent}`);
    return fetchStatus?.();
  };
  await click();
  expect(seen[0]).toMatch(/Restarting chela-collab, chela-daemon, chela-telegram/);
  expect(seen[1]).toBe('s-savemsg|Restarting chela-collab, chela-daemon, chela-telegram…'); // in progress: neither style
  expect(msg().textContent).toBe('Restarted chela-collab, chela-daemon, chela-telegram');
  expect(msg().className).toMatch(/\bok\b/);
  expect(polls).toBe(3);
  expect(posts('/api/update/apply')).toEqual([null]); // one POST, no body
});

test('a failed restart ends in the error, styled as one, with the button back', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-daemon'] });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 5, boot: BOOT, detail: 'restarting chela-daemon' };
  useStatuses([status({ applying: true }), status({ last_result: { run: 5, ok: false, step: 'pm2-restart', restarted: [], error: 'chela-daemon is still mid-tick' } })]);
  await click();
  expect(msg().textContent).toBe('Update failed at pm2-restart: chela-daemon is still mid-tick');
  expect(msg().className).toMatch(/\berr\b/);
  expect(btn()?.disabled).toBe(false);
  expect(btn()?.textContent).toBe('Restart stale services (1)');
});

test('no answer within the bound ends in "couldn\'t confirm", never success, never forever', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-collab'] });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 8, boot: BOOT, detail: 'restarting chela-collab' };
  useStatuses([new Error('down'), status({ applying: true })]);
  await click();
  expect(msg().textContent).toBe("Couldn't confirm the restart. Check `pm2 ls`.");
  expect(msg().className).toMatch(/\berr\b/);
  expect(polls).toBe(90); // 180 s at 2 s a poll
});

test('a restart of THIS dashboard (a new boot) reloads the page', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-dashboard'] });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 9, boot: BOOT, detail: 'restarting chela-dashboard' };
  useStatuses([new Error('down'), status({ boot: 'boot-2' })]);
  await click();
  expect(reloads).toBe(1);
  expect(`${msg().className}|${msg().textContent}`).toBe('s-savemsg|The dashboard restarted, reloading…');
  expect(polls).toBe(2);
});

test('a refused update surfaces the reason, naming the in-flight task, and gives the button back', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  routes['POST /api/update/apply'] = { ok: false, error: 'an update is already running (cmx-199-abc12)' };
  await click();
  expect(msg().textContent).toMatch(/cmx-199-abc12/);
  expect(msg().className).toMatch(/\berr\b/);
  expect(btn()?.disabled).toBe(false);
  expect(btn()?.textContent).toBe('Update now');
});

test('a request that fails says the dashboard may have restarted, and keeps the button off', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  routes['POST /api/update/apply'] = new Error('gone');
  await click();
  expect(msg().textContent).toBe('Request failed. The dashboard may have already restarted; refresh to check.');
  expect(btn()?.disabled).toBe(true);
});

test('a started update reports its outcome as success, not a refusal', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 3, boot: BOOT };
  useStatuses([status({ last_result: { run: 3, ok: true, step: 'done', behind_before: 3, restarted: ['chela-daemon'] } })]);
  await click();
  expect(msg().textContent).toBe('Pulled 3 commit(s). Restarted chela-daemon');
  expect(msg().className).toMatch(/\bok\b/);
  expect(msg().className).not.toMatch(/\berr\b/);
});

test('done re-reads the status: an unreadable one hides the button again', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  routes['POST /api/update/apply'] = { ok: true, started: true, run: 3, boot: BOOT };
  useStatuses([status({ last_result: { run: 3, ok: true, step: 'done', restarted: [] } })]);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  fireEvent.click(must(btn()));
  routes['/api/settings'] = new Error('down'); // only the re-read fails
  await settle(5);
  expect(row()).toMatch(/Unknown/);
  expect(btn()).toBeNull();
});

test('a not-started reply reports its detail, not a restart, and re-reads the status', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  routes['POST /api/update/apply'] = { ok: true, started: false, detail: 'already up to date' };
  const before = calls.filter((c) => c.path === '/api/settings').length;
  await click();
  expect(msg().textContent).toBe('already up to date');
  expect(msg().className).toMatch(/\bok\b/);
  expect(calls.filter((c) => c.path === '/api/settings').length).toBe(before + 1);
});

test('declining the confirm aborts before any POST', async () => {
  await openWith({ ok: true, behind: 3, branch: 'dev' });
  await click(false);
  expect(posts('/api/update/apply')).toEqual([]);
  expect(btn()?.textContent).toBe('Update now');
});

test('the restart-only confirm says so; the full update warns it restarts this dashboard', async () => {
  await openWith({ ok: true, behind: 0, branch: 'dev', stale_services: ['chela-daemon'] });
  const c = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(must(btn()));
  expect(c.mock.calls[0]?.[0]).toMatch(/^Nothing to pull\. Re-sync deps and restart only the stale/);
});

test('the status list: each service a ●/○ shape AND a word, its detail beside it', async () => {
  routes['/api/settings'] = { sections: [{ title: 'Connections', items: [
    { label: 'Telegram bridge', on: true, state: 'Connected', detail: 'chela telegram' },
    { label: 'Collab relay', on: false, state: 'Off' },
  ] }] };
  await openOn();
  const rows = [...document.querySelectorAll('#settings-status .s-status-row')];
  expect(rows.map((r) => r.textContent)).toEqual(['●ConnectedTelegram bridgechela telegram', '○OffCollab relay']);
  expect(must(rows[0]?.querySelector('.s-status-badge')).className).toBe('s-status-badge on');
  expect(must(rows[1]?.querySelector('.s-status-badge')).className).toBe('s-status-badge off');
  expect(must(document.querySelector('.s-status-grouphead')).textContent).toBe('Connections');
});

// --- the env-wins switches ----------------------------------------------------------------

const sw = (id: string) => must(document.getElementById(id), `#${id}`) as HTMLButtonElement;
const src = (id: string) => must(document.getElementById(id)).textContent;

test.each([
  ['remote-control-toggle', 'remote_control', 'remote-control', 'Saved · new sessions launch with Remote Control on'],
  ['file-drop-toggle', 'file_drop', 'file-drop', 'Saved · file drop into terminals on'],
] as const)('%s reflects GET, POSTs a toggle, and re-renders from the reply', async (id, field, base, saved) => {
  routes['GET /api/config'] = { [field]: false, [`${field}_source`]: 'dashboard' };
  routes['POST /api/config'] = (c: { body: Record<string, unknown> }) => ({ ...c.body, [`${field}_source`]: 'dashboard' });
  await openOn();
  expect(sw(id).getAttribute('role')).toBe('switch');
  expect(sw(id).getAttribute('aria-checked')).toBe('false');
  expect(src(`${base}-source`)).toBe('In effect: Off (this setting).');
  fireEvent.click(sw(id));
  await settle();
  expect(posts('/api/config')).toEqual([{ [field]: true }]);
  expect(sw(id).getAttribute('aria-checked')).toBe('true');
  expect(src(`${base}-msg`)).toBe(saved);
  expect(must(document.getElementById(`${base}-msg`)).className).toBe('s-savemsg ok');
});

test('unset reads as the built-in default (on, for Remote Control)', async () => {
  routes['GET /api/config'] = { remote_control: true, remote_control_source: 'default' };
  await openOn();
  expect(sw('remote-control-toggle').getAttribute('aria-checked')).toBe('true');
  expect(src('remote-control-source')).toBe('In effect: On (the built-in default).');
});

test.each([
  ['remote-control-toggle', 'remote_control', 'CHELA_REMOTE_CONTROL', 'remote-control'],
  ['file-drop-toggle', 'file_drop', 'CHELA_FILE_DROP', 'file-drop'],
] as const)('%s, env-locked: disabled, shows the effective value, names its env var', async (id, field, env, base) => {
  routes['GET /api/config'] = { [field]: true, [`${field}_env_locked`]: true, [`${field}_env`]: env };
  await openOn();
  expect(sw(id).disabled).toBe(true);
  expect(sw(id).getAttribute('aria-checked')).toBe('true');
  expect(src(`${base}-source`)).toBe(`In effect: On (set by ${env}; env wins, unset it to edit here).`);
});

test.each([
  ['a failed request', new Error('down'), 'Save failed. Unchanged.'],
  ['a refusal', { error: 'nope' }, 'Rejected. Unchanged.'],
])('%s says so, fails closed, and re-reads what IS stored', async (_what, reply, text) => {
  routes['GET /api/config'] = { remote_control: false };
  routes['POST /api/config'] = reply;
  await openOn();
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path === '/api/config').length;
  const before = gets();
  fireEvent.click(sw('remote-control-toggle'));
  await settle();
  expect(src('remote-control-msg')).toBe(text);
  expect(must(document.getElementById('remote-control-msg')).className).toBe('s-savemsg err');
  expect(sw('remote-control-toggle').getAttribute('aria-checked')).toBe('false');
  expect(gets()).toBe(before + 1);
});

test('the file-drop cap comes from the server', async () => {
  routes['GET /api/config'] = { file_drop: true, upload_max_mb: 40 };
  await openOn();
  expect(src('file-drop-cap')).toBe('40');
});

test('a config that cannot load leaves the switches disabled', async () => {
  routes['GET /api/config'] = new Error('down');
  await openOn();
  expect(sw('remote-control-toggle').disabled).toBe(true);
  expect(sw('tg-delete-toggle').disabled).toBe(true);
});

// --- Telegram topics (CMX-204) ------------------------------------------------------------

const cleanup = () => must(document.getElementById('tg-cleanup-btn')) as HTMLButtonElement;
const tgMsg = () => must(document.getElementById('tg-topics-msg')).textContent;

test('the switch and the cleanup button render from /api/config', async () => {
  routes['GET /api/config'] = { telegram_delete_on_close: true, telegram_archived_topics: 4 };
  await openOn();
  expect(sw('tg-delete-toggle').getAttribute('aria-checked')).toBe('true');
  expect([cleanup().textContent, cleanup().disabled]).toEqual(['Delete archived topics (4)', false]);
});

test('toggling the switch POSTs it and re-renders from the reply', async () => {
  routes['GET /api/config'] = { telegram_delete_on_close: false, telegram_archived_topics: 0 };
  routes['POST /api/config'] = { telegram_delete_on_close: true, telegram_archived_topics: 0 };
  await openOn();
  fireEvent.click(sw('tg-delete-toggle'));
  await settle();
  expect(posts('/api/config')).toEqual([{ telegram_delete_on_close: true }]);
  expect(sw('tg-delete-toggle').getAttribute('aria-checked')).toBe('true');
  expect(tgMsg()).toBe('Saved · delete on close on');
});

test('cleanup asks first; a cancel posts nothing', async () => {
  routes['GET /api/config'] = { telegram_archived_topics: 2 };
  await openOn();
  const c = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(cleanup());
  await settle();
  expect(c).toHaveBeenCalledOnce();
  expect(posts('/api/config')).toEqual([]);
});

test('a confirmed cleanup posts the request and shows it pending', async () => {
  routes['GET /api/config'] = { telegram_archived_topics: 2 };
  routes['POST /api/config'] = { telegram_archived_topics: 2, telegram_cleanup_pending: true };
  await openOn();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  fireEvent.click(cleanup());
  await settle();
  expect(posts('/api/config')).toEqual([{ telegram_cleanup: true }]);
  expect([cleanup().textContent, cleanup().disabled]).toEqual(['Deleting archived topics…', true]);
  expect(tgMsg()).toBe('Requested. The Telegram bridge deletes them on its next tick.');
});

test('a pending cleanup from an earlier visit renders disabled; nothing archived disables it too', async () => {
  routes['GET /api/config'] = { telegram_archived_topics: 3, telegram_cleanup_pending: true };
  await openOn();
  expect([cleanup().textContent, cleanup().disabled]).toEqual(['Deleting archived topics…', true]);
  routes['GET /api/config'] = { telegram_archived_topics: 0 };
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await openOn();
  expect([cleanup().textContent, cleanup().disabled]).toEqual(['Delete archived topics (0)', true]);
});

test.each([
  ['a refusal', { error: 'bad' }, 'Rejected. Unchanged.'],
  ['a failed request', new Error('down'), 'Save failed. Unchanged.'],
])('%s leaves things as they were', async (_w, reply, text) => {
  routes['GET /api/config'] = { telegram_delete_on_close: false, telegram_archived_topics: 1 };
  routes['POST /api/config'] = reply;
  await openOn();
  fireEvent.click(sw('tg-delete-toggle'));
  await settle();
  expect(tgMsg()).toBe(text);
  expect(sw('tg-delete-toggle').getAttribute('aria-checked')).toBe('false');
});

// --- agent mode and model; the projects folder --------------------------------------------

const CFG = {
  agent_permission_modes: ['default', 'auto', 'bypassPermissions'], agent_permission_mode: '', agent_permission_mode_default: 'default',
  agent_permission_mode_effective: 'default', agent_cmd_overrides: [], agent_models: ['sonnet', 'opus'], agent_model: '',
  agent_model_default: 'sonnet', agent_model_effective: 'sonnet',
};

test('the mode select lists the server\'s enum, unset reads as the default, and says what is in effect', async () => {
  routes['GET /api/config'] = { ...CFG, agent_cmd_overrides: [{ workflow: 'lean', cmd: 'claude -p' }] };
  await openOn();
  expect(must(document.getElementById('agent-mode-select')).textContent).toBe('default · built-in default');
  fireEvent.keyDown(must(document.getElementById('agent-mode-select')), { key: 'Enter' });
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['default · built-in default', 'auto · safe ops auto-approved, risky ones gated', 'bypassPermissions · no prompts at all']);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  expect(src('agent-mode-source')).toBe('In effect: claude --permission-mode default (from the built-in default). Overridden for lean (claude -p) (a workflow that pins agent.cmd wins over this setting.)');
  expect(src('agent-model-source')).toBe('Coding agents launch with --model sonnet (from the built-in default).');
});

test('choosing a mode POSTs it and reports the next dispatch; a refusal says unchanged and re-reads', async () => {
  routes['GET /api/config'] = CFG;
  routes['POST /api/config'] = { ...CFG, agent_permission_mode: 'auto', agent_permission_mode_effective: 'auto' };
  await openOn();
  await pick('agent-mode-select', 'auto · safe ops auto-approved, risky ones gated');
  expect(posts('/api/config')).toEqual([{ agent_permission_mode: 'auto' }]);
  expect(src('agent-mode-msg')).toBe('Saved · next dispatch launches in auto');
  expect(src('agent-mode-source')).toMatch(/--permission-mode auto \(from this setting\)/);
  routes['POST /api/config'] = { error: 'not a mode' };
  await pick('agent-model-select', 'opus');
  expect(src('agent-model-msg')).toBe('Rejected. Model unchanged.');
  expect(must(document.getElementById('agent-model-select')).textContent).toBe('sonnet · default');
});

test('the projects folder: stored value in the field, the effective dir as placeholder; Save (or Enter) posts it', async () => {
  routes['GET /api/config'] = { projects_dir: '~/code', projects_dir_effective: '/home/u/code' };
  routes['POST /api/config'] = { projects_dir: '~/work', projects_dir_effective: '/home/u/work' };
  await openOn();
  const input = must(document.getElementById('cfg-projects-dir')) as HTMLInputElement;
  expect([input.value, input.placeholder]).toEqual(['~/code', '/home/u/code']);
  await type(input, ' ~/work ');
  fireEvent.keyDown(input, { key: 'Enter' });
  await settle();
  expect(posts('/api/config')).toEqual([{ projects_dir: '~/work' }]);
  expect(src('cfg-projects-msg')).toBe('Saved · scanning /home/u/work');
  expect(calls.some((c) => c.path === '/api/launcher')).toBe(true); // the launch menu re-reads its suggestions
});
