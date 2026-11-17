// CMX-385: the sidebar head says who the trusted proxy attributed you as — and, in tailscale
// mode, that this is attribution only (any local process can send that header).
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { WhoAmI } from '../index.ts';

afterEach(cleanup);

test('no identity draws nothing (off mode, or an unattributed request)', () => {
  const { container } = render(<WhoAmI who={null} />);
  expect(container.innerHTML).toBe('');
});

test('a tailscale login is shown, titled as attribution only', () => {
  const { container } = render(
    <WhoAmI who={{ login: 'op@example.com', name: 'Op', mode: 'tailscale', verified: false }} />,
  );
  const chip = container.querySelector('#whoami');
  expect(chip?.textContent).toBe('op@example.com');
  expect(chip?.getAttribute('title')).toBe(
    'Signed in as op@example.com (Op), from tailscale (attribution only, not authentication)',
  );
});

test('a verified Cloudflare login says it was verified', () => {
  const { container } = render(
    <WhoAmI who={{ login: 'op@example.com', name: null, mode: 'cloudflare-access', verified: true }} />,
  );
  expect(container.querySelector('#whoami')?.getAttribute('title')).toBe(
    'Signed in as op@example.com, verified by Cloudflare Access',
  );
});
