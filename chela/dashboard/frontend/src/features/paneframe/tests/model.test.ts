import { expect, test } from 'vitest';
import { cidOfSrc, frameSrc, isPaletteKey, newCid, termSrc, uriListToText, widOfSrc } from '../model.ts';

test('the terminal path encodes the window id and ends in a slash; the frame adds only its cid', () => {
  expect(termSrc('@12')).toBe('/term/%4012/');
  expect(frameSrc('@12', 'abc')).toBe('/term/%4012/?cid=abc');
});

test('each cid is fresh', () => {
  expect(newCid()).not.toBe(newCid());
  expect(newCid().length).toBeGreaterThan(8);
});

test.each([
  ['/term/%4012/?cid=x', '@12'],
  ['http://h/base/term/%403/', '@3'],
  ['/term/@4/ws', '@4'],
  ['/term/%405', null], // no trailing slash
  ['/terminal/x', null],
  ['about:blank', null],
  ['', null],
])('widOfSrc(%s) is %s', (src, wid) => {
  expect(widOfSrc(src)).toBe(wid);
});

test.each([
  ['/term/%401/?cid=abc', 'abc'],
  ['/term/%401/?x=1&cid=a%20b', 'a b'],
  ['/term/%401/?cid=abc&x=1', 'abc'],
  ['/term/%401/?xcid=abc', null],
  ['/term/%401/?cid=', null],
  ['/term/%401/', null],
])('cidOfSrc(%s) is %s', (src, cid) => {
  expect(cidOfSrc(src)).toBe(cid);
});

test('a uri-list gives its first URL, skipping blank lines and comments', () => {
  expect(uriListToText('# c\r\n\r\n  https://a  \nhttps://b')).toBe('https://a');
  expect(uriListToText('# only a comment\n')).toBe('');
});

const key = (k: Partial<KeyboardEvent>) => ({ ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, key: 'k', ...k });

test.each([
  [key({ ctrlKey: true }), true],
  [key({ metaKey: true, key: 'K' }), true],
  [key({}), false],
  [key({ ctrlKey: true, altKey: true }), false],
  [key({ ctrlKey: true, shiftKey: true }), false],
  [key({ ctrlKey: true, key: 'j' }), false],
])('isPaletteKey(%o) is %s', (e, want) => {
  expect(isPaletteKey(e)).toBe(want);
});
