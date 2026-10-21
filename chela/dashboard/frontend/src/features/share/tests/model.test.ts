// CMX-239: the share dialog's pure rules (ported from tests/share_dialog.test.mjs, CMX-403/419).
import { expect, test } from 'vitest';
import { durLabel, isLong, mintBody, shareOffer, unsafeDurations, type ShareDraft } from '../index.ts';

test('typing is offered only with the setting on AND a verified sandbox; else the reason says which', () => {
  expect(shareOffer({ share_typing: true, sandboxed: true, typing_allowed: true })).toMatchObject({ typingOk: true, reason: '' });
  expect(shareOffer({ share_typing: false, typing_allowed: false }).reason).toBe('Typing is disabled in Settings');
  expect(shareOffer({ share_typing: true, typing_allowed: false }).reason).toBe('Not a sandboxed session. Start one from New session → Sandboxed');
  // A failed options read is {}: nothing but view only.
  expect(shareOffer({})).toMatchObject({ typingOk: false, offerUnsafe: false, reason: 'Typing is disabled in Settings', name: '', mins: 30 });
});

test.each([
  [{ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'w' }, true],
  [{ share_typing: false, sandboxed: false, unsandboxed_offered: true, window_name: 'w' }, false],
  [{ share_typing: true, sandboxed: true, unsandboxed_offered: true, window_name: 'w' }, false],
  [{ share_typing: true, sandboxed: false, unsandboxed_offered: false, window_name: 'w' }, false],
  [{ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: '' }, false],
])('the UNSANDBOXED override is offered for %j: %s', (o, offered) => {
  expect(shareOffer(o).offerUnsafe).toBe(offered);
});

test('durations: the server choices plus the default, sorted, deduplicated; 4 h is the long mark', () => {
  expect(unsafeDurations({})).toEqual({ choices: [30, 240, 1440, 10080, 20160], longMins: 240 });
  expect(unsafeDurations(null)).toEqual({ choices: [30, 240, 1440, 10080, 20160], longMins: 240 });
  expect(unsafeDurations({ unsandboxed_choices: [60, 15], unsandboxed_minutes: 45, unsandboxed_long_minutes: 120 })).toEqual({ choices: [15, 45, 60], longMins: 120 });
  expect(unsafeDurations({ unsandboxed_choices: [60, 30], unsandboxed_minutes: 60 }).choices).toEqual([30, 60]);
  expect(unsafeDurations({ unsandboxed_choices: [] }).choices).toEqual([30, 240, 1440, 10080, 20160]);
  expect(shareOffer({ unsandboxed_minutes: 240 }).mins).toBe(240);
});

test('duration labels', () => {
  expect([30, 90, 240, 1440, 10080, 20160].map(durLabel)).toEqual(['30 min', '90 min', '4 h', '1 day', '7 days', '14 days']);
});

const UNSAFE = shareOffer({ share_typing: true, sandboxed: false, unsandboxed_offered: true, window_name: 'shell-1' });
const draft = (d: Partial<ShareDraft>): ShareDraft => ({ mode: 'unsandboxed', minutes: 30, confirm: '', confirmLong: '', ...d });

test('isLong: strictly past the long mark', () => {
  expect([isLong(UNSAFE, 240), isLong(UNSAFE, 241)]).toEqual([false, true]);
});

test('view only always mints; typing only where it is offered', () => {
  expect(mintBody(UNSAFE, draft({ mode: 'view' }))).toEqual({ on: true, mode: 'view' });
  expect(mintBody(UNSAFE, draft({ mode: 'typing' }))).toBeNull();
  expect(mintBody(shareOffer({ typing_allowed: true }), draft({ mode: 'typing' }))).toEqual({ on: true, mode: 'typing' });
});

test('the override mints only with the window name typed exactly', () => {
  expect(mintBody(UNSAFE, draft({}))).toBeNull();
  expect(mintBody(UNSAFE, draft({ confirm: 'shell-2' }))).toBeNull();
  expect(mintBody(UNSAFE, draft({ confirm: ' shell-1 ' }))).toEqual({ on: true, mode: 'unsandboxed', minutes: 30, confirm: 'shell-1' });
  // Not offered (a sandboxed window): the typed name does not arm it.
  expect(mintBody(shareOffer({ share_typing: true, sandboxed: true, unsandboxed_offered: true, window_name: 'shell-1' }), draft({ confirm: 'shell-1' }))).toBeNull();
});

test('past 4 h the name must be typed a second time, and both are sent', () => {
  expect(mintBody(UNSAFE, draft({ minutes: 10080, confirm: 'shell-1' }))).toBeNull();
  expect(mintBody(UNSAFE, draft({ minutes: 10080, confirm: 'shell-1', confirmLong: 'shell-2' }))).toBeNull();
  expect(mintBody(UNSAFE, draft({ minutes: 10080, confirm: 'shell-1', confirmLong: 'shell-1' }))).toEqual({ on: true, mode: 'unsandboxed', minutes: 10080, confirm: 'shell-1', confirm_long: 'shell-1' });
  expect(mintBody(UNSAFE, draft({ minutes: 240, confirm: 'shell-1' }))).toEqual({ on: true, mode: 'unsandboxed', minutes: 240, confirm: 'shell-1' });
});
