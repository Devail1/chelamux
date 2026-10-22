// CMX-242 (CMX-212 P27): a pane's footer: the context bar (the window id, which copies itself,
// the model, the PR and cost chips on the wall, the branch, the context % with its fill strip
// and the Files chip) and, on a wall tile, the action bar (Answer / Approve sends Enter to the
// pane, Review opens the PR). From terminals.js `_ctxBarHTML`, `_applyTermContext` and
// `_applyWallTileFrame`. `wall` is a Gridstack tile; otherwise it is the single pane.
import { useLayoutEffect, useRef, useState } from 'react';
import { actionBarKind, prChip } from '../../../../../static/js/wallmodel.ts';
import { windowIdQuery } from '../../../../../static/js/windowid.ts';
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import type { ContextRow } from '../../../shared/store/context.ts';
import { TextButton } from '../../../shared/ui/index.ts';
import { openDiffModal } from '../../diffmodal/index.ts';
import { wantsHuman, type HeadAgent } from '../../panehead/index.ts';
import { ctxView, NO_CONTEXT } from '../model.ts';

export interface TileFootProps {
  wid: string;
  wall: boolean;
  agent: HeadAgent | undefined;
  ctx: ContextRow | undefined;
}

const TOAST_MS = 1500;

/** Copy `text`. The clipboard API needs a secure context; on a plain-http dashboard a selected
 * <textarea> and execCommand('copy') still copy rather than silently not (CMX-417). */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      /* unsupported */
    }
    ta.remove();
    return ok;
  }
}

/** Enter, through the whitelisted key path (app.py `_TERM_KEYS`): it accepts Claude's own
 * highlighted choice at a permission gate and submits a typed reply. */
async function sendEnter(wid: string): Promise<void> {
  try {
    await api('/api/term/key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: wid, key: 'Enter' }),
    });
  } catch (e) {
    console.error('termKey', e);
  }
}

export function TileFoot({ wid, wall, agent, ctx }: TileFootProps) {
  const bar = useRef<HTMLDivElement>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const view = ctxView(ctx);
  const wants = wantsHuman(agent);
  const action = wall ? actionBarKind(agent ?? {}, wants) : null;
  const pr = wall ? prChip(agent?.pr) : null;

  // The pane around the footer: a pane waiting on a human wears the amber border, and one with
  // an action bar gives its terminal that much more bottom room. The bar is pointer-events:
  // none, so its tooltip is mirrored onto the header, a hoverable surface.
  useLayoutEffect(() => {
    const host = bar.current?.closest('.grid-stack-item-content, .term-pane');
    if (!host) return;
    host.classList.toggle('term-waiting', wants);
    host.classList.toggle('has-term-action', !!action);
    const head = host.querySelector<HTMLElement>('.gs-head');
    if (head) head.title = view ? view.tip : '';
  });

  useLayoutEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
  }, []);

  const copyWid = async () => {
    const ok = await copyText(wid);
    setToast(ok ? `Copied ${wid}` : `Copy failed: ${wid}`);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), TOAST_MS);
  };

  const answer = async () => {
    setSending(true);
    try {
      await sendEnter(wid);
    } finally {
      setSending(false);
    }
  };

  const prTitle = pr ? `Open PR ${pr.label}${pr.repository ? ` (${pr.repository})` : ''}${pr.state === 'unknown' ? ' (state unknown)' : ''}` : undefined;
  return (
    <>
      <div className="term-ctx-bar" data-ctx-for={wid} title={view ? view.tip : NO_CONTEXT} ref={bar}>
        {/* the address the orchestrator, `chela peek @N` and peer messages use (CMX-417) */}
        {windowIdQuery(wid) && (
          <TextButton className="gs-wid" data-wid={wid} title={`tmux window ${wid}, click to copy`} onClick={(e) => {
              e.stopPropagation();
              void copyWid();
            }}>
            {wid}
          </TextButton>
        )}
        <span className="gs-model" hidden={!view?.model}>{view?.model ?? ''}</span>
        {wall && (
          <a className={pr?.dim ? 'gs-pr gs-pr-dim' : 'gs-pr'} data-pr-for={wid} hidden={!pr} href={pr?.url} title={prTitle} target="_blank" rel="noopener noreferrer">
            {/* CMX-41: the flag is earned by a confirmed open, non-draft PR only */}
            {pr?.flag && <icons.prFlag className="gs-pr-flag" size={10} aria-label="open" />}
            {pr?.label ?? ''}
          </a>
        )}
        {wall && (
          <span className="gs-cost" hidden={!view?.cost} title={view?.cost ? 'session cost' : undefined}>
            {view?.cost ?? ''}
          </span>
        )}
        <span className="gs-branch" hidden={!view?.branch} title={view?.branch ? `branch: ${view.branch}` : undefined}>
          {view?.branch && <icons.branch className="gs-branch-icon" size={11} aria-hidden />}
          {view?.branch ?? ''}
        </span>
        <span className={view?.sev ? `gs-ctx ${view.sev}` : 'gs-ctx'} hidden={!view}>{view?.chip ?? ''}</span>
        {/* CMX-299: the modal fetches on open, so this chip needs no poll */}
        <TextButton className="gs-files" title="Changed files" aria-label="Changed files" onClick={(e) => {
            e.stopPropagation();
            openDiffModal(wid);
          }}>
          <icons.files size={12} aria-hidden />
        </TextButton>
        <i className={['term-ctx-fill', view?.sev, view?.estimated ? 'est' : ''].filter(Boolean).join(' ')} style={{ width: view ? view.width : '0' }} />
        {toast && <div className="gs-wid-toast" role="status">{toast}</div>}
      </div>
      {wall && (
        <div className={action ? `term-action-bar action-${action.kind}` : 'term-action-bar'} data-action-for={wid} hidden={!action}>
          {action?.kind === 'review' && (
            <>
              <span className="term-action-label">
                <icons.done size={12} aria-hidden /> PR ready for review
              </span>
              <a className="term-action-btn" href={pr ? pr.url : '#'} target="_blank" rel="noopener noreferrer">Review</a>
            </>
          )}
          {action && action.kind !== 'review' && (
            <>
              <span className="term-action-label">
                <icons.needsAction size={11} aria-hidden /> {action.label === 'Answer' ? 'Needs an answer' : 'Needs approval'}
              </span>
              <TextButton className="term-action-btn" disabled={sending} onClick={() => void answer()}>{action.label}</TextButton>
            </>
          )}
        </div>
      )}
    </>
  );
}
