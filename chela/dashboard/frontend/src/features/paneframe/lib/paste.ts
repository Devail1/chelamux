// The keybar's keys and Paste, into the single pane. xterm.js cannot surface iOS's native
// "Paste" callout, so a tap reads the device clipboard (the gesture unlocks the read) and POSTs
// it to /api/term/paste, a bracketed paste at the tmux layer.
// CMX-423: an image (a phone screenshot) takes /api/term/paste-image, then its path is typed so
// Claude attaches it; every no-op says why on the button.
// CMX-428: ONE clipboard call per tap, read() if it exists, else readText(); a refusal is "Denied",
// never a retry through the other (on iOS the tap covers a single read). iOS often hands text
// over as text/uri-list or text/html with no text/plain entry: both are accepted.
import { api } from '../../../shared/api/index.ts';
import { uriListToText } from '../model.ts';
import { host } from './host.ts';

const postJson = (path: string, body: unknown) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** Send a named key (/api/term/key) to the single pane. */
export async function termKey(key: string): Promise<void> {
  const wid = host.activeWid();
  if (!wid) return;
  await postJson('/api/term/key', { agent: wid, key }).catch((e: unknown) => console.error('termKey', e));
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
  if (!text) return flash(btn, 'Empty');
  await postJson('/api/term/paste', { agent: wid, text });
}

async function pasteImage(wid: string, blob: Blob, btn: HTMLElement): Promise<void> {
  const fd = new FormData();
  fd.append('agent', wid);
  fd.append('image', blob, (blob as File).name || 'paste');
  const j = (await api('/api/term/paste-image', { method: 'POST', body: fd, credentials: 'same-origin' })) as { path?: string } | null;
  if (!j?.path) return flash(btn, 'Failed');
  await pasteText(wid, j.path, btn);
}

/** Plain text from a text/html entry: drop script/style, fold source whitespace, break lines at
 * <br> and block ends, strip tags, decode entities (a <textarea>'s innerHTML parses as RCDATA,
 * so no markup is ever instantiated), collapse whitespace. */
export function htmlToText(html: string): string {
  const ta = document.createElement('textarea');
  ta.innerHTML = String(html)
    .replace(/<(script|style)\b[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/\s+/g, ' ')
    .replace(/<br\b[^>]*>|<\/(p|div|li|tr|h[1-6]|pre|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]*>/g, '');
  return ta.value.split('\n').map((l) => l.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
}

const TEXT_TYPES: [string, (s: string) => string][] = [['text/plain', (t) => t], ['text/uri-list', uriListToText], ['text/html', htmlToText]];

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

const DENIED = Symbol('denied');

/** The keybar's Paste: the device clipboard into the single pane. `btn` shows the outcome. */
export async function termPaste(btn: HTMLElement): Promise<void> {
  const wid = host.activeWid();
  const cb = (navigator as Partial<Navigator>).clipboard as Partial<Clipboard> | undefined;
  if (!wid) return;
  if (!cb || (!cb.read && !cb.readText)) return flash(btn, 'No clipboard');
  try {
    const got = await (async () => (cb.read ? cb.read.call(cb) : (cb.readText as () => Promise<string>).call(cb)))().catch(() => DENIED);
    if (got === DENIED) return flash(btn, 'Denied');
    if (typeof got === 'string') return await pasteText(wid, got, btn);
    const items = (got || []) as ClipboardItem[];
    for (const it of items) {
      const t = (it.types || []).find((x) => x.startsWith('image/'));
      if (t) return await pasteImage(wid, await it.getType(t), btn);
    }
    await pasteText(wid, await itemsText(items), btn);
  } catch (e) {
    console.error('termPaste', e);
    flash(btn, 'Failed');
  }
}
