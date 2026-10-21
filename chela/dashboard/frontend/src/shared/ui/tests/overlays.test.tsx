// CMX-218: the kit's overlays keep their behaviour (Radix) under our class names.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { Share2 } from 'lucide-react';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';
import { Button, Dialog, DialogClose, Menu, Popover, Toast, ToastProvider, Tooltip } from '../index.ts';
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

test('CMX-240: the sheet variant is drawn as the safety sheets are, its title in a header with a close button', async () => {
  const onOpenChange = vi.fn();
  render(
    <Dialog open variant="sheet" icon={Share2} title="Active shares" description="Stop one to revoke it." onOpenChange={onOpenChange}>
      <p>rows</p>
    </Dialog>,
  );
  const dlg = screen.getByRole('dialog', { name: 'Active shares' });
  expect(dlg.className).toBe('shares-sheet');
  expect(must(dlg.parentElement).className).toBe('shares-backdrop ui-sheet');
  expect(dlg.querySelector('.modal-desc')).toBeNull();
  expect(must(dlg.querySelector('.ss-sub')).textContent).toBe('Stop one to revoke it.');
  const hd = must(dlg.querySelector('.ss-hd'));
  expect([hd.querySelector('.ss-hd-ic svg') !== null, hd.querySelector('h3')?.textContent]).toEqual([true, 'Active shares']);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  });
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

test('the modal variant has no sheet header or close button of its own', () => {
  render(<Dialog open title="Plain" onOpenChange={() => {}} />);
  const dlg = screen.getByRole('dialog', { name: 'Plain' });
  expect([dlg.querySelector('.ss-hd'), screen.queryByRole('button', { name: 'Close' })]).toEqual([null, null]);
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
