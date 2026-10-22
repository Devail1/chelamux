// CMX-221 (CMX-212 P16): the General tab: connections and status, the Update card, the projects
// folder, the dispatcher agent's permission mode and model, Remote Control, file drop, the
// Telegram topics (CMX-204) and the remote-access note.
import { useState } from 'react';
import { api } from '../../../shared/api/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { Button, FieldRow, Select, TextInput, Toggle } from '../../../shared/ui/index.ts';
import { refreshLauncher } from '../../launcher/index.ts';
import { followUpdate, type FollowDeps } from '../../update/flow.ts';
import { agentModeLabel, agentModelLabel, staleWords, updateView, type Config, type UpdateInfo } from '../model.ts';
import { ConfigSwitch, SaveMsg, Section, useLoad, type Msg } from './parts.tsx';
import { config, postConfig } from './state.ts';

type StatusItem = { label?: string; on?: boolean; state?: string; detail?: string };
type SettingsPayload = { sections?: { title?: string; items?: StatusItem[] }[]; update?: UpdateInfo } | null;

/** Test seam: the post-apply poll's clock, sleep, fetch and reload (features/update/flow.ts). */
export const updateDeps: FollowDeps = {};

async function loadStatus(): Promise<SettingsPayload | 'failed'> {
  try {
    return (await api('/api/settings')) as SettingsPayload;
  } catch {
    return 'failed';
  }
}

// ●/○ SHAPE + a word carry each state; colour is only a hint (Liav is red-weak).
function Badge({ on, children }: { on: boolean; children: React.ReactNode }) {
  return (
    <span className={`s-status-badge ${on ? 'on' : 'off'}`}>
      <span className="s-status-dot" aria-hidden="true">{on ? '●' : '○'}</span>{children}
    </span>
  );
}

function StatusList({ data }: { data: SettingsPayload | 'failed' | null }) {
  if (data === null) return <div className="s-status-list"><div className="s-desc">Loading…</div></div>;
  if (data === 'failed') return <div className="s-status-list"><div className="s-desc">Status unavailable.</div></div>;
  const sections = data?.sections || [];
  return (
    <div className="s-status-list">
      {!sections.length && <div className="s-desc">No status.</div>}
      {sections.map((sec, i) => (
        <div className="s-status-group" key={i}>
          <div className="s-status-grouphead">{sec.title || ''}</div>
          {(sec.items || []).map((it, j) => (
            <div className="s-status-row" key={j}>
              <Badge on={!!it.on}>{it.state || ''}</Badge>
              <span className="s-status-label">{it.label || ''}</span>
              {it.detail && <span className="s-status-detail" title={it.detail}>{it.detail}</span>}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** The Update card's status line (CMX-199): the checkout is behind, its services run stale
 * code, the freshness check could not answer, or it is up to date. */
function UpdateLine({ upd }: { upd: UpdateInfo | null | undefined }) {
  const v = updateView(upd);
  const unknownNote = 'unknown' in v && Array.isArray(v.unknown) && v.unknown.length ? ` · freshness unknown for ${v.unknown.join(', ')}` : '';
  let detail: React.ReactNode;
  if (v.kind === 'unknown') detail = v.detail;
  else if (v.kind === 'behind') detail = `branch ${v.branch}: ${v.behind} commit(s) unpulled; running services are still serving what they last loaded${unknownNote}`;
  else if (v.kind === 'stale') {
    const w = staleWords(v.stale.length);
    detail = (
      <>
        branch {v.branch}: nothing to pull, but code that {v.stale.join(', ')} {w.verb} changed after {w.pronoun} started (or{' '}
        <code className="s-cmd">pm2 restart {v.stale.map((n, i) => <span key={n}>{i ? ' ' : ''}<span className="s-cmd-arg">{n}</span></span>)}</code>){unknownNote}
      </>
    );
  } else if (v.kind === 'unreadable') {
    detail = <>branch {v.branch}: nothing to pull, but could not read which services run old code (check <code className="s-cmd">pm2 ls</code>)</>;
  } else detail = `branch ${v.branch}: nothing to pull${unknownNote}`;
  return (
    <div className="s-status-row" id="update-status-row">
      <Badge on={v.kind === 'ok'}>{v.badge}</Badge>
      <span className="s-status-detail" id="update-status-detail">{detail}</span>
    </div>
  );
}

function UpdateCard({ data, reload }: { data: SettingsPayload | 'failed' | null; reload: () => void }) {
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const upd = data === null ? undefined : data === 'failed' ? null : data?.update;
  const v = updateView(upd);
  const apply = async () => {
    const restartOnly = v.button?.mode === 'restart';
    if (!window.confirm(restartOnly
      ? 'Nothing to pull. Re-sync deps and restart only the stale chela-* services?'
      : 'Pull, re-sync, and restart every running chela-* service (including this dashboard)?')) return;
    setBusy(restartOnly ? 'Restarting…' : 'Updating…');
    setMsg({ cls: '', text: 'Starting…' });
    let resp: { error?: string; ok?: boolean; started?: boolean; detail?: string; run?: unknown; boot?: unknown } | null;
    try {
      resp = (await api('/api/update/apply', { method: 'POST' })) as typeof resp;
    } catch {
      setMsg({ cls: 'err', text: 'Request failed. The dashboard may have already restarted; refresh to check.' });
      return;
    }
    if (!resp || resp.error || resp.ok === false) {
      setMsg({ cls: 'err', text: resp?.error || 'Update refused.' });
      setBusy(null);
      return;
    }
    if (!resp.started) {
      setMsg({ cls: 'ok', text: resp.detail || 'Already up to date.' });
      setBusy(null);
      reload();
      return;
    }
    // CMX-56: every click ends in a message; the route's `detail` names what it started.
    const what = resp.detail ? resp.detail.charAt(0).toUpperCase() + resp.detail.slice(1) : 'Started';
    setMsg({ cls: 'ok', text: `${what}…` });
    // CMX-95: follow it to an outcome; a new `boot` means THIS dashboard restarted: reload.
    const step = await followUpdate(resp, (s) => {
      if (s.state === 'polling' || s.state === 'reload') setMsg({ cls: '', text: s.text });
    }, updateDeps);
    if (step.state === 'reload') return;
    setMsg({ cls: step.state === 'done' ? 'ok' : 'err', text: step.text });
    setBusy(null);
    if (step.state === 'done') reload();
  };
  return (
    <Section title="Update" id="settings-update" keywords="upgrade version git pull restart pm2">
      {data === null ? (
        <div className="s-status-row" id="update-status-row">
          <Badge on={false}>Checking…</Badge>
          <span className="s-status-detail" id="update-status-detail"></span>
        </div>
      ) : <UpdateLine upd={upd} />}
      <p className="s-desc">Pulls this checkout, re-syncs deps, and restarts every running <code>chela-*</code> PM2 service
        (including this dashboard), the same as running <code>chela update</code> from the CLI. A dirty working tree or a
        branch diverged from its upstream refuses rather than clobbering anything.</p>
      {(v.button || busy) && (
        <div className="s-row">
          <Button variant="primary" id="update-apply-btn" disabled={!!busy} onClick={() => void apply()}>{busy ?? v.button?.label}</Button>
        </div>
      )}
      <SaveMsg id="update-apply-msg" msg={msg} />
    </Section>
  );
}

function Projects() {
  const cfg = useStore(config);
  const [draft, setDraft] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const value = draft ?? cfg?.projects_dir ?? '';
  // Until /api/config lands the field is not the stored folder: a Save would clear it.
  const ready = !!cfg && !cfg.error;
  const save = async () => {
    if (!ready) return;
    setMsg({ cls: '', text: 'Saving…' });
    const r = await postConfig({ projects_dir: value.trim() });
    if (r.failed) {
      setMsg({ cls: 'err', text: 'Save failed.' });
      return;
    }
    setDraft(null);
    setMsg({ cls: 'ok', text: 'Saved · scanning ' + (r.cfg?.projects_dir_effective || value.trim()) });
    void refreshLauncher(); // new suggestions in the launch menu right away
  };
  return (
    <Section title="Projects folder" keywords="repos repositories directory path launcher">
      <p className="s-desc">Scanned for git repos to suggest in the <strong>+</strong> launch menu. Defaults to{' '}
        <code>~/projects</code> (or the <code>CHELA_PROJECTS_DIR</code> env var). Takes effect immediately, no restart.</p>
      <FieldRow id="cfg-projects-dir" label="Folder">
        <TextInput plain value={value} onChange={setDraft} placeholder={cfg?.projects_dir_effective || '~/projects'} onEnter={() => void save()} disabled={!ready} />
      </FieldRow>
      <div className="s-row"><Button variant="primary" disabled={!ready} onClick={() => void save()}>Save</Button></div>
      <SaveMsg id="cfg-projects-msg" msg={msg} />
    </Section>
  );
}

type EnumField = { list: keyof Config; stored: keyof Config; dflt: keyof Config; effective: keyof Config; post: string; noun: string };

/** A select over one of the server's enums, unset reading as the built-in default. */
function EnumRow({ id, label, keywords, f, optionLabel, saved }: { id: string; label: string; keywords: string; f: EnumField; optionLabel: (m: string, d: string) => string; saved: (eff: string) => string }) {
  const cfg = useStore(config);
  const [msg, setMsg] = useState<Msg>(null);
  const list = (cfg?.[f.list] as string[] | undefined) || [];
  const dflt = String(cfg?.[f.dflt] || '');
  const value = String(cfg?.[f.stored] || dflt);
  const change = async (v: string) => {
    setMsg({ cls: '', text: 'Saving…' });
    const r = await postConfig({ [f.post]: v });
    if (r.failed) setMsg({ cls: 'err', text: r.failed === 'request' ? `Save failed. ${f.noun} unchanged.` : `Rejected. ${f.noun} unchanged.` });
    else setMsg({ cls: 'ok', text: saved(String(r.cfg?.[f.effective] || v)) });
  };
  return (
    <>
      <FieldRow id={id} label={label} keywords={keywords}>
        {cfg?.error ? <span className="s-desc">(unavailable)</span>
          : <Select label={label} value={value} onChange={(v) => void change(v)} disabled={!list.length} options={list.map((m) => ({ value: m, label: optionLabel(m, dflt) }))} />}
      </FieldRow>
      <SaveMsg id={`${id.replace(/-select$/, '')}-msg`} msg={msg} />
    </>
  );
}

function AgentMode() {
  const cfg = useStore(config);
  const overrides = cfg?.agent_cmd_overrides || [];
  const live = cfg && !cfg.error;
  return (
    <Section title="Dispatcher agent mode" id="settings-agentmode">
      <p className="s-desc">Permission mode for agents the <strong>dispatcher</strong> spawns. Applies to the{' '}
        <strong>next</strong> dispatch. An agent already running keeps the mode it started with. Only the mode is settable;
        the rest of the launch command is fixed in code.</p>
      <EnumRow id="agent-mode-select" label="Permission mode" keywords="bypass approve prompts auto" optionLabel={agentModeLabel}
        f={{ list: 'agent_permission_modes', stored: 'agent_permission_mode', dflt: 'agent_permission_mode_default', effective: 'agent_permission_mode_effective', post: 'agent_permission_mode', noun: 'Mode' }}
        saved={(eff) => 'Saved · next dispatch launches in ' + eff} />
      {/* A WORKFLOW.md that pins agent.cmd SHADOWS this setting for that workflow: say so. */}
      <p className="s-desc" id="agent-mode-source">{live && (
        <>In effect: <code>claude --permission-mode {cfg.agent_permission_mode_effective || ''}</code> (from {cfg.agent_permission_mode ? 'this setting' : 'the built-in default'}).
          {overrides.length > 0 && (
            <> <strong>Overridden</strong> for {overrides.map((o, i) => <span key={o.workflow}>{i ? ', ' : ''}<code>{o.workflow}</code> (<code>{o.cmd}</code>)</span>)} (a workflow that pins <code>agent.cmd</code> wins over this setting.)</>
          )}
        </>
      )}</p>
      <EnumRow id="agent-model-select" label="Model" keywords="sonnet opus haiku llm coding agent" optionLabel={agentModelLabel}
        f={{ list: 'agent_models', stored: 'agent_model', dflt: 'agent_model_default', effective: 'agent_model_effective', post: 'agent_model', noun: 'Model' }}
        saved={(eff) => 'Saved · next dispatch launches with --model ' + eff} />
      <p className="s-desc" id="agent-model-source">{live && (
        <>Coding agents launch with <code>--model {cfg.agent_model_effective || ''}</code> (from {cfg.agent_model ? 'this setting' : 'the built-in default'}).</>
      )}</p>
      <p className="s-desc">The <strong>coding</strong> model: cmx tasks rarely need Opus, so Sonnet is the default
        (cheaper/faster). The <strong>judge</strong> (the adversarial reviewer) always runs on a capable model and is not
        affected by this setting.</p>
    </Section>
  );
}

/** CMX-204: delete a topic when its window closes, and a one-shot cleanup. The bridge makes the
 * Telegram calls on its next tick; the dashboard only stores the ask. */
function TelegramTopics() {
  const cfg = useStore(config);
  const [msg, setMsg] = useState('');
  const live = !!cfg && !cfg.error;
  const n = cfg?.telegram_archived_topics || 0;
  const pending = !!cfg?.telegram_cleanup_pending;
  const post = async (payload: Record<string, unknown>, ok: string) => {
    const r = await postConfig(payload);
    setMsg(r.failed ? (r.failed === 'request' ? 'Save failed. Unchanged.' : 'Rejected. Unchanged.') : ok);
  };
  const cleanup = () => {
    if (!window.confirm('Delete every archived Telegram topic chela remembers? This deletes their messages too and cannot be undone.')) return;
    void post({ telegram_cleanup: true }, 'Requested. The Telegram bridge deletes them on its next tick.');
  };
  return (
    <Section title="Telegram topics" id="settings-telegram-topics">
      <p className="s-desc">A new window reopens the archived topic of its folder instead of creating another. Applies on the
        bridge's next tick, no restart.</p>
      <FieldRow id="tg-delete-toggle" label="Telegram: delete a topic when its window closes" keywords="telegram topic forum delete archive close cleanup">
        <Toggle value={!!cfg?.telegram_delete_on_close} disabled={!live}
          onChange={(on) => void post({ telegram_delete_on_close: on }, 'Saved · delete on close ' + (on ? 'on' : 'off'))} />
      </FieldRow>
      <div className="s-row">
        <Button id="tg-cleanup-btn" disabled={!live || !n || pending} onClick={cleanup}>
          {pending ? 'Deleting archived topics…' : `Delete archived topics (${n})`}
        </Button>
      </div>
      <SaveMsg id="tg-topics-msg" msg={msg ? { cls: '', text: msg } : null} />
    </Section>
  );
}

export function General() {
  const [data, , reload] = useLoad(loadStatus);
  const cfg = useStore(config);
  return (
    <>
      <Section title="Connections & Status" id="settings-status" keywords="health connected daemon telegram services">
        <StatusList data={data} />
      </Section>
      <UpdateCard data={data} reload={reload} />
      <Projects />
      <AgentMode />
      <Section title="Remote Control" id="settings-remote-control">
        <p className="s-desc">Launch sessions with Claude Code's <code>--remote-control</code> so they can be driven from
          claude.ai: windows opened from the dashboard, Telegram <code>/new</code>, and the orchestrator. Applies to{' '}
          <strong>new</strong> sessions only: a window already running keeps the flag it was launched with. Never applied to
          dispatcher agents or judges.</p>
        <ConfigSwitch id="remote-control-toggle" field="remote_control" env="CHELA_REMOTE_CONTROL"
          label="Remote Control: make new sessions reachable from claude.ai" keywords="claude.ai remote phone mobile anywhere"
          saved={(on) => 'Saved · new sessions launch with Remote Control ' + (on ? 'on' : 'off')} />
      </Section>
      <Section title="File drop into terminals" id="settings-file-drop">
        <p className="s-desc">Drop a file on a Wall terminal (or paste one, e.g. a screenshot) and it is saved to that
          session's <code>uploads/</code> folder and its <code>@uploads/&lt;name&gt;</code> is typed into the prompt (not
          sent). Works from your phone too. Never overwrites; <span id="file-drop-cap">{cfg?.upload_max_mb || 25}</span> MB
          per file. Share guests can never upload. A pane picks up a change when it reloads.</p>
        <ConfigSwitch id="file-drop-toggle" field="file_drop" env="CHELA_FILE_DROP"
          label="File drop into terminals: save dropped or pasted files into the session"
          keywords="upload drag drop paste file image screenshot attach uploads phone"
          saved={(on) => 'Saved · file drop into terminals ' + (on ? 'on' : 'off')} />
      </Section>
      <TelegramTopics />
      <Section title="Remote access" keywords="tailscale ssh tunnel vpn auth security">
        <p className="s-desc">Zero built-in auth: the dashboard binds <code>127.0.0.1</code>. Put it behind a tailnet or SSH
          tunnel; that is the trust boundary.</p>
        <div className="s-examples">
          <div className="s-ex"><span className="s-tag">tailnet</span><code>tailscale serve 5001</code></div>
          <div className="s-ex"><span className="s-tag">tunnel</span><code>ssh -L 5001:127.0.0.1:5001 host</code></div>
        </div>
        <p className="s-desc">Phone: SSH/Mosh in (Blink / Termius), then <code>tmux attach</code> for the live panes.</p>
      </Section>
    </>
  );
}
