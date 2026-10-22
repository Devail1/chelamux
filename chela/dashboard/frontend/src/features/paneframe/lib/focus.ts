// The focus ring: `.term-focused` on the pane whose terminal holds the keyboard. Polled, not
// window 'blur': clicking from one frame straight into another never refocuses the parent.
//
// CMX-104: the parent's activeElement is NOT the authority (live, it still named the frame Liav
// had left while he typed into the other). Each frame's OWN document.hasFocus() is: the focus
// xterm's cursor and tmux's focus-events follow. activeElement is the fallback only when no
// frame can answer or the whole window is unfocused (a screenshot tool steals OS focus: keep
// marking the pane focus returns to). Every tick re-asserts exactly one `.term-focused`.

/** `fn()`, or undefined when it throws (a cross-document access the browser refuses). */
export function attempt<T>(fn: () => T): T | undefined {
  try {
    return fn();
  } catch {
    return undefined;
  }
}

let poll: ReturnType<typeof setInterval> | null = null;
let focused: Element | null = null;

function frameDocHasFocus(frame: HTMLIFrameElement): boolean | null {
  const doc = attempt(() => frame.contentDocument);
  if (!doc || typeof doc.hasFocus !== 'function') return null; // can't tell
  return attempt(() => doc.hasFocus()) ?? null;
}

function focusedTermFrame(): HTMLIFrameElement | null {
  const frames = [...document.querySelectorAll<HTMLIFrameElement>('iframe.term-frame')];
  const owner = frames.find((f) => frameDocHasFocus(f) === true);
  if (owner) return owner;
  const ae = document.activeElement;
  // tagName, not instanceof: the frame may come from another realm's constructors
  const aeFrame = ae?.tagName === 'IFRAME' && ae.classList.contains('term-frame') ? (ae as HTMLIFrameElement) : null;
  if (!aeFrame) return null;
  // The page has focus but this frame's own document says it does not: the keyboard is NOT
  // there, whatever activeElement claims (the CMX-104 bug).
  if ((attempt(() => document.hasFocus()) ?? true) && frameDocHasFocus(aeFrame) === false) return null;
  return aeFrame;
}

/** One tick: ring the pane holding the keyboard, clear every other ring. */
export function applyTermFocus(): void {
  const frame = focusedTermFrame();
  const pane = frame ? frame.closest('.grid-stack-item-content, .term-pane') : null;
  document.querySelectorAll('.term-focused').forEach((el) => {
    if (el !== pane) el.classList.remove('term-focused');
  });
  pane?.classList.add('term-focused');
  focused = pane;
}

/** The ringed pane (Focus mode picks it as the large pane first). */
export const focusedPane = (): Element | null => focused;

export function startFocusTracking(): void {
  if (poll) return;
  poll = setInterval(applyTermFocus, 250);
  window.addEventListener('focus', applyTermFocus); // back in the parent: clear it promptly
}

/** Move the keyboard into a pane's terminal. CMX-114: contentWindow.focus() alone does not
 * reliably move it from another focused pane (the ring moves, typing does not), so focus the
 * element too and route into xterm (`term`, ttyd's page global). All three, unconditionally. */
export function focusFrame(ifr: HTMLIFrameElement): void {
  attempt(() => ifr.contentWindow?.focus());
  attempt(() => ifr.focus());
  attempt(() => (ifr.contentWindow as (Window & { term?: { focus?: () => void } }) | null)?.term?.focus?.());
}
