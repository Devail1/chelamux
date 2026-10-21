import { act } from 'react';
import { afterEach, expect, test } from 'vitest';
import { createIslandRoot, mountIsland } from '../index.tsx';

afterEach(() => {
  document.body.innerHTML = '';
});

function Greet({ who }: { who: string }) {
  return <b>hi {who}</b>;
}

test('mountIsland renders the component with its props into the element with that id', async () => {
  document.body.innerHTML = '<div id="other">keep</div><div id="slot"></div>';
  let root = null;
  await act(async () => {
    root = mountIsland('slot', Greet, { who: 'ada' });
  });
  expect(root).not.toBeNull();
  expect(document.getElementById('slot')?.innerHTML).toBe('<b>hi ada</b>');
  expect(document.getElementById('other')?.textContent).toBe('keep');
  // CMX-301: its host is marked for nav.js's touch tap-tip; the page's other elements are not.
  expect(document.getElementById('slot')?.getAttribute('data-island')).toBe('');
  expect(document.getElementById('other')?.hasAttribute('data-island')).toBe(false);
});

test('createIslandRoot marks its host [data-island] and renders into it', async () => {
  const el = document.createElement('div');
  document.body.append(el);
  const root = createIslandRoot(el);
  expect(el.getAttribute('data-island')).toBe('');
  await act(async () => {
    root.render(<Greet who="bo" />);
  });
  expect(el.innerHTML).toBe('<b>hi bo</b>');
});

test('mountIsland mounts nothing and returns null when the page has no such element', async () => {
  document.body.innerHTML = '<div id="slot"></div>';
  let root: unknown = 'unset';
  await act(async () => {
    root = mountIsland('missing', Greet, { who: 'ada' });
  });
  expect(root).toBeNull();
  expect(document.body.innerHTML).toBe('<div id="slot"></div>');
});
