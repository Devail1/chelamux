// The on-screen keys (CMX-237). The pinning/fitting geometry on a real page (events, the pane,
// ttyd's term.fit) is tests/keybar_pane_fit.test.mjs; real layout, tests/browser/.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { termMode } from '../../../shared/store/wall.ts';
import { configurePaneFrames } from '../../paneframe/index.ts';
import { Keybar, paneKeybarActions } from '../index.ts';
import { afterCtrlLetter, keyboardOccluded, nextCtrl, paneFitHeight, pinTop } from '../model.ts';

function draw() {
  const actions = { termKey: vi.fn<(key: string) => void>(), termPaste: vi.fn<(btn: HTMLButtonElement) => void>() };
  const { container } = render(<Keybar actions={actions} />);
  return { el: container, actions, sent: () => actions.termKey.mock.calls.map((c) => c[0]) };
}
const phone = (el: HTMLElement) => el.querySelector('#term-keybar') as HTMLElement;
const desk = (el: HTMLElement) => el.querySelector('#term-bar') as HTMLElement;
const byText = (root: HTMLElement, text: string) => {
  const b = [...root.querySelectorAll('button')].find((x) => x.textContent === text);
  if (!b) throw new Error(`no button ${text}`);
  return b;
};

beforeEach(() => {
  localStorage.clear();
  act(() => termMode.set(null));
});
afterEach(cleanup);

test('Ctrl cycles off, armed, locked, off; a letter disarms armed and keeps locked', () => {
  expect([nextCtrl('off'), nextCtrl('armed'), nextCtrl('locked')]).toEqual(['armed', 'locked', 'off']);
  expect([afterCtrlLetter('off'), afterCtrlLetter('armed'), afterCtrlLetter('locked')]).toEqual(['off', 'off', 'locked']);
});

test('the phone row: tapping Ctrl advances data-ctrl; a Ctrl-letter sends C-<letter> and consumes an armed Ctrl', () => {
  const { el, sent } = draw();
  const bar = phone(el);
  const ctrl = bar.querySelector('#kb2-ctrl') as HTMLElement;
  const letter = (title: string) => bar.querySelector(`.kb2-cl[title="${title}"]`) as Element;
  expect(bar.dataset.ctrl).toBe('off');
  expect(ctrl.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(ctrl);
  expect(bar.dataset.ctrl).toBe('armed');
  expect(ctrl.getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(letter('Ctrl-D (EOF)'));
  expect(sent()).toEqual(['C-d']);
  expect(bar.dataset.ctrl).toBe('off');
  fireEvent.click(ctrl);
  fireEvent.click(ctrl);
  expect(bar.dataset.ctrl).toBe('locked');
  fireEvent.click(letter('Ctrl-Z (suspend)'));
  fireEvent.click(letter('Ctrl-A'));
  expect(sent()).toEqual(['C-d', 'C-z', 'C-a']);
  expect(bar.dataset.ctrl).toBe('locked');
  fireEvent.click(ctrl);
  expect(bar.dataset.ctrl).toBe('off');
});

test('the phone row sends each key by its /api/term/key name, in order', () => {
  const { el, sent, actions } = draw();
  const bar = phone(el);
  const keys = [...bar.querySelectorAll<HTMLButtonElement>('.kb2-key')].filter((b) => !b.closest('.kb2-ctrl-letters') && b.id !== 'kb2-ctrl' && b.textContent !== 'Paste');
  keys.forEach((b) => fireEvent.click(b));
  expect(sent()).toEqual(['Escape', 'Tab', 'BTab', 'Enter', 'C-c', 'Left', 'Up', 'Down', 'Right', '|', '/', '\\', '~', '-', '_', 'Home', 'End', 'PageUp', 'PageDown']);
  expect(bar.querySelectorAll('.kb2-ctrl-letters .kb2-cl').length).toBe(9);
  expect(screen.getAllByLabelText('Shift Tab')[0]?.textContent).toBe('⇧Tab');
  expect(byText(bar, '^C').className).toBe('kb2-key kb2-strong');
  expect(bar.querySelectorAll('.kb2-sep').length).toBe(4);
  expect(actions.termPaste).not.toHaveBeenCalled();
});

test('Paste hands terminals.js the Paste button itself (it flashes its result on it)', () => {
  const { el, actions } = draw();
  const paste = byText(phone(el), 'Paste');
  fireEvent.click(paste);
  expect(actions.termPaste.mock.calls).toEqual([[paste]]);
  expect(paste.title).toBe('Paste from clipboard');
});

test('the desktop bar shows only in single mode, and only after the Wall rendered', () => {
  const { el } = draw();
  expect(desk(el).style.display).toBe('none');
  act(() => termMode.set('single'));
  expect(desk(el).style.display).toBe('flex');
  act(() => termMode.set('wall'));
  expect(desk(el).style.display).toBe('none');
});

test('the desktop bar is collapsed by default; a toggle opens it and is remembered', () => {
  const { el } = draw();
  const tog = el.querySelector('#kb-toggle') as HTMLElement;
  expect(desk(el).className).toBe('kb-collapsed');
  expect(tog.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(tog);
  expect(desk(el).className).toBe('');
  expect(tog.getAttribute('aria-expanded')).toBe('true');
  expect(localStorage.getItem('pc_kb_collapsed')).toBe('0');
  cleanup();
  const again = draw();
  expect(desk(again.el).className).toBe('');
  fireEvent.click(again.el.querySelector('#kb-toggle') as HTMLElement);
  expect(localStorage.getItem('pc_kb_collapsed')).toBe('1');
  expect(desk(again.el).className).toBe('kb-collapsed');
});

test('an unreadable localStorage still draws the bar, collapsed, and a toggle still works', () => {
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('SecurityError');
  });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('QuotaExceededError');
  });
  const { el } = draw();
  expect(desk(el).className).toBe('kb-collapsed');
  fireEvent.click(el.querySelector('#kb-toggle') as HTMLElement);
  expect(desk(el).className).toBe('');
  get.mockRestore();
  set.mockRestore();
});

test('the desktop keys send their names; the D-pad keeps its grid classes', () => {
  const { el, sent } = draw();
  const bar = desk(el);
  for (const t of ['Escape', 'Tab', 'Enter', 'Ctrl-C (interrupt)', 'Ctrl-D (EOF)', 'Ctrl-L (clear)', 'Ctrl-B (tmux prefix)', 'Up', 'Left', 'Down', 'Right', 'Home', 'Page Up', 'End', 'Page Down']) {
    fireEvent.click(bar.querySelector(`button[title="${t}"]`) as Element);
  }
  expect(sent()).toEqual(['Escape', 'Tab', 'Enter', 'C-c', 'C-d', 'C-l', 'C-b', 'Up', 'Left', 'Down', 'Right', 'Home', 'PageUp', 'End', 'PageDown']);
  expect([...bar.querySelectorAll('.kb-dpad button')].map((b) => b.className)).toEqual(['kb-up', 'kb-left', 'kb-down', 'kb-right']);
});

test('Scroll enters tmux copy-mode, and the same button leaves it', () => {
  const { el, sent } = draw();
  const btn = el.querySelector('#term-scroll-btn') as HTMLElement;
  expect(btn.textContent?.trim()).toBe('Scroll');
  fireEvent.click(btn);
  expect(sent()).toEqual(['scroll']);
  expect(btn.textContent).toBe('Exit Scroll');
  expect(btn.className).toBe('btn-accent');
  fireEvent.click(btn);
  expect(sent()).toEqual(['scroll', 'scroll-exit']);
  expect(btn.textContent?.trim()).toBe('Scroll');
  expect(btn.className).toBe('');
});

test('the default actions send the single pane\'s key through features/paneframe', async () => {
  const sent: { url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    sent.push({ url, body: JSON.parse(String(init?.body)) });
    return Promise.resolve({ json: () => Promise.resolve({}) });
  });
  configurePaneFrames({ activeWid: () => '@7', tick() {}, frameLoaded() {} });
  paneKeybarActions.termKey('Tab');
  await act(async () => {});
  expect(sent).toEqual([{ url: '/api/term/key', body: { agent: '@7', key: 'Tab' } }]);
  vi.unstubAllGlobals();
});

test('the phone row stops listening once it unmounts', () => {
  const add = vi.spyOn(window, 'addEventListener');
  const remove = vi.spyOn(window, 'removeEventListener');
  draw();
  const added = add.mock.calls.filter((c) => c[0] === 'resize').map((c) => c[1]);
  expect(added.length).toBe(1);
  cleanup();
  expect(remove.mock.calls.filter((c) => c[0] === 'resize').map((c) => c[1])).toEqual(added);
  add.mockRestore();
  remove.mockRestore();
});

test('the geometry: what the keyboard covers (phone only), where the bar sits, how tall the pane is', () => {
  expect(keyboardOccluded({ mobile: true, innerHeight: 844, vvTop: 0, vvHeight: 508 })).toBe(336);
  expect(keyboardOccluded({ mobile: true, innerHeight: 844, vvTop: 40, vvHeight: 508 })).toBe(296);
  expect(keyboardOccluded({ mobile: true, innerHeight: 844, vvTop: 0, vvHeight: 900 })).toBe(0);
  expect(keyboardOccluded({ mobile: false, innerHeight: 844, vvTop: 0, vvHeight: 508 })).toBe(0);
  expect(keyboardOccluded({ mobile: true, innerHeight: 844, vvTop: 0, vvHeight: 507.6 })).toBe(336);
  expect(pinTop({ vvTop: 0, vvHeight: 508, barHeight: 47 })).toBe(461);
  expect(pinTop({ vvTop: 30, vvHeight: 508, barHeight: 47.4 })).toBe(491);
  expect(pinTop({ vvTop: 0, vvHeight: 20, barHeight: 47 })).toBe(0);
  const fit = { mobile: true, occluded: 336, vvTop: 0, vvHeight: 508, barHeight: 47, paneTop: 98 };
  expect(paneFitHeight(fit)).toBe(363);
  expect(paneFitHeight({ ...fit, barHeight: 47.4 })).toBe(362);
  expect(paneFitHeight({ ...fit, mobile: false })).toBeNull();
  expect(paneFitHeight({ ...fit, occluded: 0 })).toBeNull();
  expect(paneFitHeight({ ...fit, vvHeight: 100 })).toBe(0);
});
