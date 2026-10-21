// CMX-239: a throwaway bubble under a pane's header, gone after 4 s (the wire's join and error
// notes use it). Plain DOM: the header it lands in is still legacy markup.
export function paneToast(anchor: Element | null, msg: string): void {
  const host = anchor?.closest('.gs-head') ?? anchor?.parentElement;
  if (!host) {
    console.log('[chela-share]', msg);
    return;
  }
  host.querySelectorAll('.term-share-toast').forEach((t) => t.remove());
  const t = document.createElement('div');
  t.className = 'term-share-toast';
  t.textContent = msg;
  host.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
