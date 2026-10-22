// The focus ring: `.term-focused` on the pane whose terminal holds the keyboard.
//
// The terminals are same-origin iframes, so we POLL rather than rely on window 'blur': blur
// fires when focus first leaves the parent into a frame, but clicking from one frame straight
// into another never refocuses the parent, so blur alone leaves the ring on the first pane.
//
// CMX-104: the parent's activeElement is NOT the authority. Live, Liav typed into one pane (its
// xterm drew the solid cursor) while the ring sat on the other, because activeElement still
// named the frame he had left. The authority is each frame's OWN document.hasFocus(), the focus
// xterm's cursor and tmux's focus-events follow; activeElement is the fallback only when no
// frame can answer or the whole window is unfocused (a screenshot tool steals OS focus: keep
// marking the pane focus returns to). Every tick re-asserts exactly one `.term-focused`.

const FOCUS_POLL_MS = 250;
let poll: ReturnType<typeof setInterval> | null = null;
let focused: Element | null = null;

function frameDocHasFocus(frame: HTMLIFrameElement): boolean | null {
  let doc: Document | null = null;
  try {
    doc = frame.contentDocument;
  } catch {
    /* cross-doc guard */
  }
  if (!doc || typeof doc.hasFocus !== 'function') return null; // can't tell
  try {
    return doc.hasFocus();
  } catch {
    return null;
  }
}

function focusedTermFrame(): HTMLIFrameElement | null {
  const frames = [...document.querySelectorAll<HTMLIFrameElement>('iframe.term-frame')];
  const owner = frames.find((f) => frameDocHasFocus(f) === true);
  if (owner) return owner;
  const ae = document.activeElement;
  // tagName, not instanceof: the frame may come from another realm's constructors
  const aeFrame = ae?.tagName === 'IFRAME' && ae.classList.contains('term-frame') ? (ae as HTMLIFrameElement) : null;
  if (!aeFrame) return null;
  let pageFocused = true;
  try {
    pageFocused = document.hasFocus();
  } catch {
    /* assume focused */
  }
  // The page has focus but this frame's own document says it does not: the keyboard is NOT
  // there, whatever activeElement claims (the CMX-104 bug).
  if (pageFocused && frameDocHasFocus(aeFrame) === false) return null;
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
  poll = setInterval(applyTermFocus, FOCUS_POLL_MS);
  window.addEventListener('focus', applyTermFocus); // back in the parent: clear it promptly
}

/** Move the keyboard into a pane's terminal. CMX-114: contentWindow.focus() alone does not
 * reliably move it when switching FROM another focused pane (the ring moves, typing does not
 * follow), so focus the element too and route into xterm (`term`, ttyd's page global). All
 * three, unconditionally: that is what moves live browser focus. */
export function focusFrame(ifr: HTMLIFrameElement): void {
  try {
    ifr.contentWindow?.focus();
  } catch {
    /* cross-doc guard */
  }
  try {
    ifr.focus();
  } catch {
    /* cross-doc guard */
  }
  try {
    const t = (ifr.contentWindow as (Window & { term?: { focus?: () => void } }) | null)?.term;
    if (t && typeof t.focus === 'function') t.focus();
  } catch {
    /* cross-doc guard */
  }
}
