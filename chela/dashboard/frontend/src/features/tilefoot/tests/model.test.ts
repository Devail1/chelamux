// CMX-242: what a footer says, pure (from terminals.js `_applyTermContext`).
import { expect, test } from 'vitest';
import { ctxView, NO_CONTEXT } from '../index.ts';

test('no row, or a row without used_pct, is no reading', () => {
  expect(ctxView(undefined)).toBeNull();
  expect(ctxView(null)).toBeNull();
  expect(ctxView({ window_id: '@1', model: 'm' })).toBeNull();
  expect(NO_CONTEXT).toBe('Context: --');
});

test('a full reading', () => {
  expect(ctxView({ used_pct: 54, used: '147.5K', total: '1M', model: 'opus', cost_usd: 1.5, branch: 'b' })).toEqual({
    width: '54%', sev: '', estimated: false, chip: '54% · 147.5K/1M',
    tip: 'Context: 147.5K/1M (54%) · opus · $1.5', model: 'opus', cost: '$1.50', branch: 'b',
  });
});

test('0% is a reading, not a missing one', () => {
  expect(ctxView({ used_pct: 0, used: '0', total: '1M' })?.chip).toBe('0% · 0/1M');
  expect(ctxView({ used_pct: 0 })?.width).toBe('0%');
});

test('the thresholds: above 60 warns, above 80 is danger', () => {
  expect(ctxView({ used_pct: 60 })?.sev).toBe('');
  expect(ctxView({ used_pct: 61 })?.sev).toBe('ctx-warn');
  expect(ctxView({ used_pct: 80 })?.sev).toBe('ctx-warn');
  expect(ctxView({ used_pct: 81 })?.sev).toBe('ctx-danger');
});

test('an estimate is marked in the chip and the tooltip', () => {
  const v = ctxView({ used_pct: 25, used: '50k', total: '200k', estimated: true });
  expect(v?.estimated).toBe(true);
  expect(v?.chip).toBe('25%~ · 50k/200k');
  expect(v?.tip).toBe('Context: 50k/200k (25%~) · window size estimated');
  expect(ctxView({ used_pct: 30, used: '300K', total: '1M', estimated: true, window_source: 'usage' })?.tip)
    .toBe('Context: 300K/1M (30%~) · window inferred as 1M from usage past 200K');
  expect(ctxView({ used_pct: 30, used: '150K', total: '500K', estimated: true, window_source: 'config' })?.tip)
    .toBe('Context: 150K/500K (30%~) · window from the configured default');
});

// CMX-475: the states that carry no %.
test('no turn yet reads "—", never 0%', () => {
  const v = ctxView({ state: 'no_turns', used_pct: null, model: 'opus' });
  expect(v).toMatchObject({ chip: '—', width: '0%', sev: '', tip: 'Context: — (no turn yet) · opus' });
  expect(v?.chip).not.toContain('0%');
});

test('after /compact: "compacted", never the stale pre-compact %', () => {
  const v = ctxView({ state: 'compacted', used_pct: null, pre: '818.3K' });
  expect(v).toMatchObject({ chip: 'compacted', width: '0%', sev: '' });
  expect(v?.tip).toBe('Context: compacted (was 818.3K); the real % shows after the next turn');
  expect(ctxView({ state: 'compacted' })?.tip).toBe('Context: compacted; the real % shows after the next turn');
  // a compacted row that still carried a % must not show it
  expect(ctxView({ state: 'compacted', used_pct: 82 })?.chip).toBe('compacted');
});

test('an unknown window shows absolute tokens, no %', () => {
  const v = ctxView({ used: '430K', used_pct: null, total: null, window_source: 'unknown' });
  expect(v).toMatchObject({ chip: '430K', width: '0%', tip: 'Context: 430K tokens · window size unknown' });
  expect(v?.chip).not.toContain('%');
});

test('missing pieces read as absent, never as text', () => {
  const v = ctxView({ used_pct: 5 });
  expect(v).toMatchObject({ model: null, cost: null, branch: null, chip: '5%' });
  expect(ctxView({ used_pct: 5, cost_usd: 0 })?.cost).toBe('$0.00');
  expect(ctxView({ used_pct: 101 })?.width).toBe('100%');
});
