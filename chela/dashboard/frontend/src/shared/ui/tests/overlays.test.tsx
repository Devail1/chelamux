// CMX-218: the kit's overlays keep their behaviour (Radix) under our class names.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';
import { AnchoredPopover, Button, Dialog, DialogClose, ItemButton, Menu, Popover, Sheet, Toast, ToastProvider, Tooltip } from '../index.ts';
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
  expect(dialog.parentElement?.className).toBe('modal-overlay active');
  expect(dialog.querySelector('h3')?.textContent).toBe('New task');
  // No description: no aria-describedby pointing at an element that is not there.
  expect(dialog.hasAttribute('aria-describedby')).toBe(false);
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
  expect(must(sheet.parentElement).className).toBe('shares-backdrop');
  expect(must(sheet.querySelector('.ss-hd')).textContent).toBe('Pending approvals');
  const close = screen.getByRole('button', { name: 'Close' });
  expect(close.className).toBe('ss-close');
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

test('a click on a Toast closes it; a click on a link inside it does not', () => {
  const onOpenChange = vi.fn();
  render(<ToastProvider><Toast open onOpenChange={onOpenChange} title={<>Run <a href="https://x.test">PR</a></>} /></ToastProvider>);
  fireEvent.click(screen.getByText('PR'));
  expect(onOpenChange).not.toHaveBeenCalled();
  fireEvent.click(must(document.querySelector('.run-toast')));
  expect(onOpenChange).toHaveBeenCalledWith(false);
});
