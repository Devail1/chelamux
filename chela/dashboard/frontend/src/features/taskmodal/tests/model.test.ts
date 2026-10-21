// The task modal's pure model (static/js/taskmodalmodel.js until CMX-228; ported from
// tests/taskmodal_model.test.mjs, taskmodal_review_tabs.test.mjs and taskmodal_judge_badge.test.mjs;
// the key, tracker link and risk chip are the runs model's, tested with it). Each check goes red under one specific corruption of the
// logic it names.
import { expect, test } from 'vitest';
import { CI_CHIP, JUDGE_BADGE, briefSource, ciKind, defaultTab, displayTitle, heldOutCount, judgeBadgeClass, statusLabel, timelineSteps, wfShort } from '../model.ts';

// --- briefSource: brief > body > raw, never throws on a sparse item

test('briefSource: a run brief wins over body and raw; then body; then raw', () => {
  expect(briefSource({ brief: 'RUN BRIEF', body: 'TASK BODY', raw: '- [ ] raw line' })).toBe('RUN BRIEF');
  expect(briefSource({ brief: null, body: 'TASK BODY', raw: '- [ ] raw line' })).toBe('TASK BODY');
  expect(briefSource({ brief: null, body: null, raw: '- [ ] raw line' })).toBe('- [ ] raw line');
});

test('briefSource: empty strings are absent, not a match; nothing at all is null', () => {
  expect(briefSource({ brief: '', body: '', raw: 'raw wins' })).toBe('raw wins');
  expect(briefSource({})).toBeNull();
  expect(briefSource(null)).toBeNull();
  expect(briefSource(undefined)).toBeNull();
});

// --- displayTitle: display-only concise header, never the parsed title

test('displayTitle: a leading bold span becomes the display title, trailing text dropped', () => {
  const raw = "**📥 Move the Decisions log to the wiki.** Design is SETTLED — build EXACTLY as spec'd.";
  expect(displayTitle(raw)).toBe('📥 Move the Decisions log to the wiki.');
});

test('displayTitle: no leading bold span (or bold mid-string) is returned unchanged; empty is ""', () => {
  expect(displayTitle('plain one-line task title')).toBe('plain one-line task title');
  expect(displayTitle('do the thing with **emphasis** in the middle')).toBe('do the thing with **emphasis** in the middle');
  expect(displayTitle('')).toBe('');
  expect(displayTitle(null)).toBe('');
  expect(displayTitle(undefined)).toBe('');
});

// --- timelineSteps: never throws, always an ordered list

test('timelineSteps: null/empty/malformed JSON and a non-list all resolve to []', () => {
  for (const bad of [null, '', undefined, '{not json', '{"round":1}', '"just a string"', '42']) expect(timelineSteps(bad)).toEqual([]);
});

test('timelineSteps: non-object entries are dropped, valid ones map in order', () => {
  const raw = JSON.stringify([
    { round: 1, at: '2026-07-20T10:00:00+00:00', body: 'fix the flaky test', verdict: 'changes_requested' },
    'a stray string entry',
    null,
    42,
    [1],
    { round: 2, at: '2026-07-20T11:00:00+00:00', body: 'looks good now', verdict: 'reopened', mutations: [{}, {}] },
  ]);
  expect(timelineSteps(raw)).toEqual([
    { round: 1, at: '2026-07-20T10:00:00+00:00', state: 'changes_requested', detail: 'fix the flaky test', visible: 0, heldOut: 0 },
    { round: 2, at: '2026-07-20T11:00:00+00:00', state: 'reopened', detail: 'looks good now', visible: 2, heldOut: 0 },
  ]);
});

test('timelineSteps: a missing verdict/body/round/at degrades to safe defaults', () => {
  expect(timelineSteps(JSON.stringify([{ body: 7 }]))).toEqual([{ round: null, at: null, state: 'review', detail: '', visible: 0, heldOut: 0 }]);
});

test('heldOutCount reads the count from its own heading line only, all its digits', () => {
  expect(heldOutCount('x\n### 🙈 2 held-out guard(s) also survived')).toBe(2);
  expect(heldOutCount('### 🙈 12 held-out guard(s)')).toBe(12);
  for (const neg of ['🙈 2 held-out guard(s)', '#### 🙈 2 held-out guard', 'x ### 🙈 2 held-out guard', null, 7]) expect(heldOutCount(neg)).toBe(0);
});

// --- defaultTab (CMX-122)

test.each([
  [{ status: 'awaiting_review' }, 2, 'review'],
  [{ status: 'needs_human' }, 1, 'review'],
  [{ status: 'running', judge_state: 'blocked' }, 1, 'review'],
  [{ status: 'changes_requested', judge_state: 'blocked_race' }, 1, 'review'],
  [{ status: 'running' }, 2, 'brief'],
  [{ status: 'done', judge_state: 'clean' }, 2, 'brief'],
  [{ status: 'awaiting_review' }, 0, 'brief'],
])('defaultTab(%j, %i) is %s', (item, rounds, want) => {
  expect(defaultTab(item, rounds)).toBe(want);
});

test('defaultTab: no item is the Brief tab', () => {
  expect(defaultTab(null, 3)).toBe('brief');
});

test('wfShort: the workflow file\'s directory; statusLabel: underscores to spaces', () => {
  expect(wfShort('/x/chelamux/WORKFLOW.md')).toBe('chelamux');
  expect(wfShort('/x/chelamux/WORKFLOW.md/')).toBe('chelamux');
  expect(wfShort('WORKFLOW.md')).toBe('WORKFLOW.md');
  expect(wfShort(undefined)).toBe('');
  expect(statusLabel('awaiting_review')).toBe('awaiting review');
  expect(statusLabel(undefined)).toBe('?');
});

test('the CI chip: each known state its own word and class; anything else is "ci unknown"', () => {
  expect(CI_CHIP[ciKind('passing')]).toEqual({ label: 'ci green', cls: 'ci-passing' });
  expect(CI_CHIP[ciKind('failing')]).toEqual({ label: 'CI RED', cls: 'ci-failing' });
  expect(CI_CHIP[ciKind('pending')]).toEqual({ label: 'ci pending', cls: 'ci-pending' });
  expect(CI_CHIP[ciKind('none')]).toEqual({ label: 'no ci', cls: 'ci-none' });
  for (const other of ['unknown', 'weird', undefined, 'toString']) expect(ciKind(other)).toBe('unknown');
});

// --- the judge badge

// 🔴 GUARD (CMX-239 round 2): `blocked_race` is a CONFIRMED blocking finding the run row never
// recorded: exactly as loud as `blocked`, never `cannot_verify`'s low-priority tier.
test('the judge badge: blocked_race is as loud as blocked; an unknown state is the low tier', () => {
  expect(JUDGE_BADGE).toEqual({ clean: 'badge-on', blocked: 'badge-off', running: 'badge-awaiting', cannot_verify: 'badge-priority-low', blocked_race: 'badge-off' });
  expect(judgeBadgeClass('blocked_race')).toBe('badge-off');
  expect(judgeBadgeClass('something_new')).toBe('badge-priority-low');
  expect(judgeBadgeClass('constructor')).toBe('badge-priority-low');
});
