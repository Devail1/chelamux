// CMX-218: the kit's overlays keep their behaviour (Radix) under our class names.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { Share2 } from 'lucide-react';
import { useState, type ReactElement } from 'react';
import { describe, expect, test, vi } from 'vitest';
import { AnchoredPopover, Button, Dialog, DialogClose, ItemButton, Menu, Popover, Sheet, TextInput, Toast, ToastProvider, Tooltip, onOverlayOpen } from '../index.ts';
import { must } from './setup.ts';

function DialogHarness({ onOpenChange = () => {} }: { onOpenChange?: (o: boolean) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open</Button>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          onOpenChange(o);
          setOpen(o);
        }}
        title="New task"
        className="modal-newtask"
      >
        <input aria-label="Title" />
        <DialogClose asChild>
          <Button>Cancel</Button>
        </DialogClose>
      </Dialog>
    </>
  );
}

test('a Dialog without a title throws: it is the accessible name', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  expect(() => render(<Dialog open onOpenChange={() => {}} title="  " />)).toThrow(/needs a title/);
  vi.restoreAllMocks();
});

test('the Dialog is named by its title and drawn with .modal-overlay / .modal', () => {
  render(<DialogHarness />);
  fireEvent.click(screen.getByText('Open'));
  const dialog = screen.getByRole('dialog', { name: 'New task' });
  expect(dialog.className).toBe('modal modal-newtask');
  expect(dialog.parentElement?.className).toBe('modal-overlay active ui-overlay');
  expect(dialog.querySelector('h3')?.textContent).toBe('New task');
  // No description: no aria-describedby pointing at an element that is not there.
  expect(dialog.hasAttribute('aria-describedby')).toBe(false);
});

test('CMX-306: focus opens on the content\'s first field, not on the corner close button', async () => {
  render(<DialogHarness />);
  fireEvent.click(screen.getByText('Open'));
  await act(async () => {});
  expect(document.activeElement).toBe(screen.getByLabelText('Title'));
});

test('focus moves into the Dialog, Escape closes it, and focus returns to the opener', async () => {
  const onOpenChange = vi.fn();
  render(<DialogHarness onOpenChange={onOpenChange} />);
  const opener = screen.getByText('Open');
  opener.focus();
  fireEvent.click(opener);
  const dialog = screen.getByRole('dialog');
  await act(async () => {});
  expect(dialog.contains(document.activeElement)).toBe(true);
  fireEvent.keyDown(must(document.activeElement), { key: 'Escape' });
  // Radix moves focus out on the next macrotask.
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(onOpenChange.mock.calls).toEqual([[false]]);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
});

test('DialogClose closes it too', async () => {
  render(<DialogHarness />);
  fireEvent.click(screen.getByText('Open'));
  fireEvent.click(screen.getByText('Cancel'));
  await act(async () => {});
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a description is wired as the Dialog\'s description', () => {
  render(<Dialog open onOpenChange={() => {}} title="Kill" description="This ends the session." />);
  const dialog = screen.getByRole('dialog');
  const desc = screen.getByText('This ends the session.');
  expect(desc.className).toBe('modal-desc');
  expect(dialog.getAttribute('aria-describedby')).toBe(desc.id);
});

test('CMX-240: the sheet variant is drawn as the safety sheets are, its title in a header with a close button', async () => {
  const onOpenChange = vi.fn();
  render(
    <Dialog open variant="sheet" icon={Share2} title="Active shares" description="Stop one to revoke it." onOpenChange={onOpenChange}>
      <p>rows</p>
    </Dialog>,
  );
  const dlg = screen.getByRole('dialog', { name: 'Active shares' });
  expect(dlg.className).toBe('shares-sheet');
  expect(must(dlg.parentElement).className).toBe('shares-backdrop ui-sheet ui-overlay');
  expect(dlg.querySelector('.modal-desc')).toBeNull();
  expect(must(dlg.querySelector('.ss-sub')).textContent).toBe('Stop one to revoke it.');
  const hd = must(dlg.querySelector('.ss-hd'));
  expect([hd.querySelector('.ss-hd-ic svg') !== null, hd.querySelector('h3')?.textContent]).toEqual([true, 'Active shares']);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  });
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

test('the modal variant has no sheet header; its close button is the corner x (CMX-306)', () => {
  const onOpenChange = vi.fn();
  render(<Dialog open title="Plain" onOpenChange={onOpenChange} />);
  const dlg = screen.getByRole('dialog', { name: 'Plain' });
  expect(dlg.querySelector('.ss-hd')).toBeNull();
  const close = screen.getByRole('button', { name: 'Close' });
  expect(close.className).toBe('task-modal-close ui-close');
  fireEvent.click(close);
  expect(onOpenChange.mock.calls).toEqual([[false]]);
});

test('Popover opens from its trigger as a .popover', () => {
  render(
    <Popover trigger={<Button>More</Button>} label="More actions" className="launch-menu">
      <p>inside</p>
    </Popover>,
  );
  expect(screen.queryByText('inside')).toBeNull();
  fireEvent.click(screen.getByText('More'));
  const box = screen.getByRole('dialog', { name: 'More actions' });
  expect(box.className).toBe('popover ui-floating launch-menu');
  expect(box.textContent).toBe('inside');
});

test('Menu: keyboard opens it, items are .popover-item menuitems, Enter picks one', () => {
  const pick = vi.fn();
  const never = vi.fn();
  render(<Menu trigger={<Button>Row</Button>} items={[{ label: 'Rename', onSelect: pick }, 'separator', { label: 'Kill', onSelect: never, disabled: true }]} />);
  fireEvent.keyDown(screen.getByText('Row'), { key: 'Enter' });
  const menu = screen.getByRole('menu', { name: 'Row' });
  expect(menu.className).toBe('popover overflow-menu ui-floating');
  const items = screen.getAllByRole('menuitem');
  expect(items.map((i) => [i.className, i.textContent])).toEqual([
    ['popover-item ov-item', 'Rename'],
    ['popover-item ov-item', 'Kill'],
  ]);
  expect(menu.querySelectorAll('.popover-sep')).toHaveLength(1);
  fireEvent.click(must(items[1]));
  expect(never).not.toHaveBeenCalled();
  fireEvent.keyDown(must(items[0]), { key: 'Enter' });
  expect(pick).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('menu')).toBeNull();
});

test('Tooltip shows on focus as a .tap-tip', async () => {
  render(
    <Tooltip content="Set by CHELA_X">
      <Button>knob</Button>
    </Tooltip>,
  );
  fireEvent.focus(screen.getByText('knob'));
  await act(async () => {});
  const tip = screen.getByRole('tooltip');
  expect(tip.textContent).toBe('Set by CHELA_X');
  expect(document.querySelector('.tap-tip.ui-floating')).not.toBeNull();
});

// CMX-301: iOS treats a tap that makes hover content appear as a hover and drops its click, so
// the kit's Tooltip must never open on a touch pointer; a mouse hover still opens it.
test.each([
  ['touch', false],
  ['mouse', true],
] as const)('Tooltip on a %s pointer: opens = %s', (pointerType, opens) => {
  render(
    <Tooltip content="Set by CHELA_X">
      <Button>knob</Button>
    </Tooltip>,
  );
  const knob = screen.getByText('knob');
  vi.useFakeTimers();
  try {
    fireEvent.pointerMove(knob, { pointerType });
    act(() => {
      vi.advanceTimersByTime(450);   // past the 400ms delayDuration
    });
    expect(screen.queryByRole('tooltip') !== null).toBe(opens);
  } finally {
    vi.useRealTimers();
  }
});

test('Toast lands in the .run-toast-stack as a .run-toast, and leaves on close', () => {
  const { rerender } = render(
    <ToastProvider>
      <Toast open onOpenChange={() => {}} title="CMX-1 merged">
        into dev
      </Toast>
    </ToastProvider>,
  );
  const stack = must(document.querySelector('.run-toast-stack'));
  const toast = must(stack.querySelector('.run-toast'));
  expect(toast.textContent).toBe('CMX-1 mergedinto dev');
  rerender(
    <ToastProvider>
      <Toast open={false} onOpenChange={() => {}} title="CMX-1 merged" />
    </ToastProvider>,
  );
  expect(stack.querySelector('.run-toast')).toBeNull();
});

test('a Toast with no body renders its title alone', () => {
  render(
    <ToastProvider>
      <Toast open onOpenChange={() => {}} title="Saved" />
    </ToastProvider>,
  );
  const toast = must(document.querySelector('.run-toast'));
  expect(toast.children).toHaveLength(1);
  expect(toast.textContent).toBe('Saved');
});

// CMX-219: the safety sheets' Dialog, the triggerless popover, the popover row and the menu's footer.

function SheetHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open sheet</Button>
      <Sheet open={open} onOpenChange={setOpen} title="Pending approvals" className="approvals-sheet">
        <button type="button">Approve</button>
      </Sheet>
    </>
  );
}

test('a Sheet without a title throws: it is the accessible name', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  expect(() => render(<Sheet open onOpenChange={() => {}} title=" " />)).toThrow(/needs a title/);
  vi.restoreAllMocks();
});

test('a Sheet is a named dialog in the shares-sheet look; its x and Escape close it, focus returns', async () => {
  render(<SheetHarness />);
  const opener = screen.getByText('Open sheet');
  opener.focus();
  fireEvent.click(opener);
  const sheet = screen.getByRole('dialog', { name: 'Pending approvals' });
  expect(sheet.className).toBe('shares-sheet approvals-sheet');
  expect(must(sheet.parentElement).className).toBe('shares-backdrop ui-overlay');
  expect(must(sheet.querySelector('.ss-hd')).textContent).toBe('Pending approvals');
  const close = screen.getByRole('button', { name: 'Close' });
  expect(close.className).toBe('ss-close ui-close');
  fireEvent.click(close);
  expect(screen.queryByRole('dialog')).toBeNull();
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(document.activeElement).toBe(opener);
  fireEvent.click(opener);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('a Sheet whose opener left the page does not throw on close', async () => {
  function Gone() {
    const [open, setOpen] = useState(false);
    const [shown, setShown] = useState(true);
    return (
      <>
        {shown && <Button onClick={() => { setOpen(true); setShown(false); }}>Go</Button>}
        <Sheet open={open} onOpenChange={setOpen} title="S"><span /></Sheet>
      </>
    );
  }
  render(<Gone />);
  const go = screen.getByText('Go');
  go.focus();
  fireEvent.click(go);
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('an AnchoredPopover is open exactly while it has an anchor, named and classed; Escape asks to close', () => {
  const anchor = document.createElement('button');
  document.body.appendChild(anchor);
  const onClose = vi.fn();
  const { rerender } = render(<AnchoredPopover anchor={null} onClose={onClose} className="launch-menu" label="New">x</AnchoredPopover>);
  expect(document.querySelector('.popover')).toBeNull();
  rerender(<AnchoredPopover anchor={anchor} onClose={onClose} className="launch-menu" label="New"><ItemButton>Row</ItemButton></AnchoredPopover>);
  const pop = must(document.querySelector<HTMLElement>('.popover'));
  expect([pop.className, pop.getAttribute('aria-label')]).toEqual(['popover ui-floating launch-menu', 'New']);
  fireEvent.keyDown(pop, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
  // focus moving away (to the control that opened it) does not close it
  anchor.focus();
  fireEvent.focusIn(anchor);
  expect(onClose).toHaveBeenCalledTimes(1);
  anchor.remove();
});

test('ItemButton is a real button drawn as a .popover-item row', () => {
  render(<ItemButton className="lr-main" title="t">Go</ItemButton>);
  const b = screen.getByRole('button', { name: 'Go' });
  expect([b.className, b.getAttribute('type'), b.title]).toEqual(['popover-item lr-main', 'button', 't']);
});

// CMX-309: Lucide draws 24px when unsized, which overflowed the 16px .ov-ic slot next to 13px
// labels. The kit sizes every item's icon once (the legacy overflow menu's 14px).
test('Menu: every item icon renders at 14px, never Lucide\'s 24px default', () => {
  render(<Menu trigger={<Button>Icons</Button>} items={[{ label: 'Share', icon: Share2, onSelect: () => {} }, 'separator', { label: 'Again', icon: Share2, onSelect: () => {} }]} />);
  fireEvent.keyDown(screen.getByText('Icons'), { key: 'Enter' });
  const svgs = [...screen.getByRole('menu').querySelectorAll('.ov-ic svg')];
  expect(svgs).toHaveLength(2);
  for (const svg of svgs) {
    expect(svg.getAttribute('width')).toBe('14');
    expect(svg.getAttribute('height')).toBe('14');
  }
});

test('a Menu draws its footer after its items, and is named by its trigger, whatever the trigger\'s id', () => {
  render(<Menu items={[{ label: 'One', onSelect: () => {} }]} footer={<div className="ov-stat">5h usage</div>} trigger={<Button>Open menu</Button>} />);
  fireEvent.keyDown(screen.getByText('Open menu'), { key: 'Enter' });
  const menu = screen.getByRole('menu', { name: 'Open menu' });
  expect(must(menu.lastElementChild).className).toBe('ov-stat');
  fireEvent.keyDown(menu, { key: 'Escape' });
  render(<Menu items={[{ label: 'Two', onSelect: () => {} }]} trigger={<Button id="my-trigger">Mine</Button>} />);
  fireEvent.keyDown(screen.getByText('Mine'), { key: 'Enter' });
  expect(screen.getByRole('menu', { name: 'Mine' }).getAttribute('aria-labelledby')).toBe('my-trigger');
});

test('Dialog: focus returns to the opener even when a field inside took focus with autoFocus (CMX-228)', async () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>opener</button>
        <Dialog open={open} onOpenChange={setOpen} title="Auto">
          <TextInput label="first" value="" onChange={() => {}} autoFocus />
        </Dialog>
      </>
    );
  }
  render(<Harness />);
  const opener = screen.getByRole('button', { name: 'opener' });
  act(() => opener.focus());
  fireEvent.click(opener);
  expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'first' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(document.activeElement).toBe(opener);
});

test('Dialog: one dialog handing over to another leaves focus in the new one', async () => {
  function Harness() {
    const [which, setWhich] = useState<'a' | 'b' | null>(null);
    return (
      <>
        <button onClick={() => setWhich('a')}>opener</button>
        <Dialog open={which === 'a'} onOpenChange={(o) => !o && setWhich(null)} title="A">
          <button onClick={() => setWhich('b')}>to b</button>
        </Dialog>
        <Dialog open={which === 'b'} onOpenChange={(o) => !o && setWhich(null)} title="B">
          <button>in b</button>
        </Dialog>
      </>
    );
  }
  render(<Harness />);
  const opener = screen.getByRole('button', { name: 'opener' });
  act(() => opener.focus());
  fireEvent.click(opener);
  fireEvent.click(screen.getByRole('button', { name: 'to b' }));
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(screen.getByRole('dialog', { name: 'B' }).contains(document.activeElement)).toBe(true);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(document.activeElement).toBe(opener); // B returns to what A was opened from
});

test('an AnchoredPopover hands focus back to the control it hung off when it closes', async () => {
  const anchor = document.createElement('button');
  document.body.appendChild(anchor);
  const { rerender } = render(<AnchoredPopover anchor={anchor} onClose={() => {}} label="New"><ItemButton>Row</ItemButton></AnchoredPopover>);
  await act(() => new Promise((r) => setTimeout(r, 0)));
  screen.getByRole('button', { name: 'Row' }).focus();
  rerender(<AnchoredPopover anchor={null} onClose={() => {}} label="New"><ItemButton>Row</ItemButton></AnchoredPopover>);
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(document.activeElement).toBe(anchor);
  anchor.remove();
});

test('Menu: a row can carry its own class, title, data attributes, trailing content and a press handler', () => {
  const press = vi.fn();
  render(
    <Menu trigger={<Button>Pane</Button>}
      items={[
        { label: 'Wire', onSelect: () => {}, className: 'gs-port', title: 'drag to a peer', data: { 'data-wid': '@1' }, onMouseDown: press, trailing: <span className="tail">3</span> },
        { label: 'Plain', onSelect: () => {} },
      ]} />,
  );
  fireEvent.keyDown(screen.getByText('Pane'), { key: 'Enter' });
  const [wire, plain] = screen.getAllByRole('menuitem');
  expect(wire?.className).toBe('popover-item ov-item gs-port');
  expect(wire?.getAttribute('title')).toBe('drag to a peer');
  expect(wire?.getAttribute('data-wid')).toBe('@1');
  expect(wire?.querySelector('.ov-label + .tail')?.textContent).toBe('3');
  fireEvent.mouseDown(must(wire));
  expect(press).toHaveBeenCalledTimes(1);
  expect(plain?.className).toBe('popover-item ov-item');
  expect(plain?.hasAttribute('title')).toBe(false);
});

// CMX-306: every overlay of the kit, on a phone: one z-index layer (.ui-overlay), a close button
// (.ui-close, the 44px touch target), a backdrop tap that closes it, and the drawer shut as it
// opens (onOverlayOpen). One row per surface, so a new variant without them fails here.
const SURFACES: [name: string, draw: (p: { open: boolean; onOpenChange: (o: boolean) => void }) => ReactElement, closeClass: string][] = [
  ['modal', (p) => <Dialog {...p} title="T" />, 'task-modal-close ui-close'],
  ['sheet', (p) => <Dialog {...p} title="T" variant="sheet" />, 'ss-close ui-close'],
  ['settings', (p) => <Dialog {...p} title="T" variant="settings" />, 'icon-btn ui-close'],
  ['palette', (p) => <Dialog {...p} title="T" variant="palette" hideTitle />, 'task-modal-close ui-close palette-close'],
  ['shortcuts', (p) => <Dialog {...p} title="T" variant="shortcuts" />, 'icon-btn ui-close'],
  ['Sheet', (p) => <Sheet {...p} title="T" />, 'ss-close ui-close'],
];

describe.each(SURFACES)('CMX-306: the %s overlay', (_name, draw, closeClass) => {
  const mount = (open = true) => {
    const onOpenChange = vi.fn();
    const r = render(draw({ open, onOpenChange }));
    return { onOpenChange, r, overlay: () => must(screen.getByRole('dialog').parentElement) };
  };

  test('is on the overlay layer, with a close button that closes it', () => {
    const { onOpenChange, overlay } = mount();
    expect(overlay().classList.contains('ui-overlay')).toBe(true);
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close.className).toBe(closeClass);
    fireEvent.click(close);
    expect(onOpenChange.mock.calls).toEqual([[false]]);
  });

  // a tap: the press and the click on the same element
  const tap = (el: Element) => {
    fireEvent.pointerDown(el);
    fireEvent.click(el);
  };

  test('a tap on the backdrop closes it; one inside it does not', () => {
    const { onOpenChange, overlay } = mount();
    tap(screen.getByRole('dialog'));
    expect(onOpenChange).not.toHaveBeenCalled();
    tap(overlay());
    expect(onOpenChange.mock.calls).toEqual([[false]]);
  });

  test('a press that starts inside it and ends on the backdrop (a text selection) leaves it open', () => {
    const { onOpenChange, overlay } = mount();
    fireEvent.pointerDown(screen.getByRole('dialog'));
    fireEvent.click(overlay());
    expect(onOpenChange).not.toHaveBeenCalled();
    // and that press is spent: a later click with no press of its own does not close it either
    fireEvent.click(overlay());
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  test('a backdrop press that never clicked there, then a click inside it (it bubbles up), leaves it open', () => {
    const { onOpenChange, overlay } = mount();
    fireEvent.pointerDown(overlay()); // released outside the window: no click on the backdrop
    fireEvent.click(screen.getByRole('dialog')); // a click that lands inside it and bubbles up
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  test('a backdrop tap while a floating layer is open above it leaves the dialog open', () => {
    const { onOpenChange, overlay } = mount();
    const wrapper = document.createElement('div');
    wrapper.setAttribute('data-radix-popper-content-wrapper', '');
    document.body.appendChild(wrapper);
    try {
      tap(overlay());
      expect(onOpenChange).not.toHaveBeenCalled();
    } finally {
      wrapper.remove();
    }
  });

  test('opening it runs the overlay-open listeners once; closed, it runs none', () => {
    const fn = vi.fn();
    const off = onOverlayOpen(fn);
    try {
      const { r } = mount(false);
      expect(fn).not.toHaveBeenCalled();
      r.rerender(draw({ open: true, onOpenChange: () => {} }));
      expect(fn).toHaveBeenCalledTimes(1);
      r.rerender(draw({ open: false, onOpenChange: () => {} }));
      expect(fn).toHaveBeenCalledTimes(1);
    } finally {
      off();
    }
  });
});

test('CMX-306: a listener that throws neither stops the overlay opening nor the next listener; off() unsubscribes', () => {
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  const after = vi.fn();
  const offBad = onOverlayOpen(() => { throw new RangeError('boom'); });
  const offAfter = onOverlayOpen(after);
  try {
    const r = render(<Dialog open title="T" onOpenChange={() => {}} />);
    expect(screen.getByRole('dialog', { name: 'T' })).toBeTruthy();
    expect(after).toHaveBeenCalledTimes(1);
    expect(err.mock.calls[0]?.[0]).toBe('an overlay-open listener failed');
    offAfter();
    r.rerender(<Dialog open={false} title="T" onOpenChange={() => {}} />);
    r.rerender(<Dialog open title="T" onOpenChange={() => {}} />);
    expect(after).toHaveBeenCalledTimes(1);
  } finally {
    offBad();
    offAfter();
    err.mockRestore();
  }
});

test.each([
  ['Dialog', (open: boolean, set: (o: boolean) => void) => <Dialog open={open} onOpenChange={set} title="T" />],
  ['Sheet', (open: boolean, set: (o: boolean) => void) => <Sheet open={open} onOpenChange={set} title="T" />],
])('CMX-306: a %s opened from a control now off screen (the shut drawer) does not send focus back there', async (_n, draw) => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>Opener</Button>
        {draw(open, setOpen)}
      </>
    );
  }
  render(<Harness />);
  const opener = screen.getByText('Opener');
  // laid out at the drawer's place once it has slid away: left of the viewport
  opener.getBoundingClientRect = () => ({ left: -250, right: -150, top: 10, bottom: 50, width: 100, height: 40, x: -250, y: 10, toJSON: () => ({}) });
  opener.focus();
  fireEvent.click(opener);
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).not.toBe(opener);
  // on screen again, it gets focus back
  opener.getBoundingClientRect = () => ({ left: 10, right: 110, top: 10, bottom: 50, width: 100, height: 40, x: 10, y: 10, toJSON: () => ({}) });
  opener.focus();
  fireEvent.click(opener);
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(document.activeElement).toBe(opener);
});
