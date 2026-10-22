// CMX-306: any kit overlay opening shuts the phone drawer (legacy closeSidebar()), so Settings
// opened from the drawer's gear is not drawn behind it. The desktop rail never folds for it.
import { render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Dialog, Sheet } from '../../../shared/ui/index.ts';
import { toggleSidebar } from '../controller.ts';

const phone = (yes: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({ matches: yes && q === '(max-width: 768px)' }));

beforeEach(() => {
  document.body.className = '';
  document.body.innerHTML = '<aside class="sidebar"></aside><div class="drawer-scrim" id="sidebar-scrim"></div>';
});
afterEach(() => vi.unstubAllGlobals());

const drawerOpen = () => [
  document.querySelector('.sidebar')?.classList.contains('open'),
  document.getElementById('sidebar-scrim')?.classList.contains('open'),
  document.body.classList.contains('sidebar-open'),
];

test.each([
  ['a Dialog', (open: boolean) => <Dialog open={open} onOpenChange={() => {}} title="Settings" variant="settings" />],
  ['a Sheet', (open: boolean) => <Sheet open={open} onOpenChange={() => {}} title="Pending approvals" />],
])('on a phone, %s opening shuts the open drawer', (_name, draw) => {
  phone(true);
  toggleSidebar(true);
  expect(drawerOpen()).toEqual([true, true, true]);
  const r = render(draw(false));
  expect(drawerOpen()).toEqual([true, true, true]); // a closed overlay leaves it alone
  r.rerender(draw(true));
  expect(drawerOpen()).toEqual([false, false, false]);
});

test('on a desktop, an overlay opening never folds the sidebar rail', () => {
  phone(false);
  render(<Dialog open onOpenChange={() => {}} title="Settings" variant="settings" />);
  expect(document.body.classList.contains('sidebar-collapsed')).toBe(false);
});
