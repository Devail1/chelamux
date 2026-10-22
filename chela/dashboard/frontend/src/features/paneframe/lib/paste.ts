// The keybar's keys and Paste, into the single pane (from terminals.js). xterm.js cannot
// surface iOS's native "Paste" callout in its hidden textarea, so phones read the device
// clipboard on the tap (the gesture unlocks the read) and POST it to /api/term/paste, which
// delivers a bracketed paste at the tmux layer.
//
// CMX-423: ONE clipboard.read() where it exists, like the in-pane Ctrl/Cmd+V shim. An image (a
// phone screenshot) takes /api/term/paste-image, then its path is typed, so Claude attaches it.
// Every no-op says why on the button.
// CMX-428: exactly ONE clipboard call per tap: read() if it exists, else readText(), never both.
// On iOS WebKit the tap's activation covers a single read, so a readText() after an awaited
// read() came back '' and the button said "Empty" over a full clipboard. iOS also often hands
// copied text over as text/uri-list or text/html with no text/plain entry: both are accepted.
import { api } from '../../../shared/api/index.ts';
import { uriListToText } from '../model.ts';
import { host } from './host.ts';

/** Send a named key (/api/term/key) to the single pane. */
export async function termKey(key: string): Promise<void> {
  const wid = host.activeWid();
  if (!wid) return;
  try {
    await api('/api/term/key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: wid, key }),
    });
  } catch (e) {
    console.error('termKey', e);
  }
}

const flashes = new WeakMap<HTMLElement, ReturnType<typeof setTimeout>>();

function flash(btn: HTMLElement, label: string): void {
  const orig = btn.dataset.label || btn.textContent || '';
  btn.dataset.label = orig;
  btn.textContent = label;
  clearTimeout(flashes.get(btn));
  flashes.set(btn, setTimeout(() => { btn.textContent = orig; }, 1500));
}

async function pasteText(wid: string, text: string, btn: HTMLElement): Promise<void> {
  if (!text) {
    flash(btn, 'Empty');
    return;
  }
  await api('/api/term/paste', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent: wid, text }),
  });
}

async function pasteImage(wid: string, blob: Blob, btn: HTMLElement): Promise<void> {
  const fd = new FormData();
  fd.append('agent', wid);
  fd.append('image', blob, (blob as File).name || 'paste');
  const j = (await api('/api/term/paste-image', { method: 'POST', body: fd, credentials: 'same-origin' })) as { path?: string } | null;
  if (!j?.path) {
    flash(btn, 'Failed');
    return;
  }
  await pasteText(wid, j.path, btn);
}

/** Plain text from a text/html entry: drop script/style, fold source whitespace, break lines at
 * <br> and block ends, strip tags, decode entities (a <textarea>'s innerHTML parses as RCDATA,
 * so no markup is ever instantiated), collapse whitespace. */
export function htmlToText(html: string): string {
  const stripped = String(html)
    .replace(/<(script|style)\b[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/\s+/g, ' ')
    .replace(/<br\b[^>]*>|<\/(p|div|li|tr|h[1-6]|pre|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]*>/g, '');
  const ta = document.createElement('textarea');
  ta.innerHTML = stripped;
  return ta.value
    .split('\n')
    .map((l) => l.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n');
}

const TEXT_TYPES: [string, (s: string) => string][] = [
  ['text/plain', (t) => t],
  ['text/uri-list', uriListToText],
  ['text/html', htmlToText],
];

/** The best text in clipboard.read()'s items: text/plain, then text/uri-list, then text/html. */
async function itemsText(items: readonly ClipboardItem[]): Promise<string> {
  for (const [type, conv] of TEXT_TYPES) {
    for (const it of items) {
      if (!(it.types || []).includes(type)) continue;
      const text = conv(await (await it.getType(type)).text());
      if (text) return text;
    }
  }
  return '';
}

/** The keybar's Paste: the device clipboard into the single pane. `btn` shows the outcome. */
export async function termPaste(btn: HTMLElement): Promise<void> {
  const wid = host.activeWid();
  const cb = (navigator as Partial<Navigator>).clipboard as Partial<Clipboard> | undefined;
  if (!wid) return;
  if (!cb || (!cb.read && !cb.readText)) {
    flash(btn, 'No clipboard');
    return;
  }
  try {
    if (!cb.read) {
      let text: string;
      try {
        text = await (cb.readText as () => Promise<string>).call(cb);
      } catch {
        flash(btn, 'Denied');
        return;
      }
      await pasteText(wid, text, btn);
      return;
    }
    let items: ClipboardItem[];
    try {
      items = (await cb.read.call(cb)) || [];
    } catch {
      flash(btn, 'Denied');
      return;
    }
    for (const it of items) {
      const t = (it.types || []).find((x) => x.startsWith('image/'));
      if (t) {
        await pasteImage(wid, await it.getType(t), btn);
        return;
      }
    }
    await pasteText(wid, await itemsText(items), btn);
  } catch (e) {
    console.error('termPaste', e);
    flash(btn, 'Failed');
  }
}
