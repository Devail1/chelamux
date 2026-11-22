// CMX-388 / CMX-463: with dispatch off by choice, a session groups by its own folder
// (`plainFolderKey`). A missing row (no primary window) must land in the Other section,
// not throw on `a.cwd`.
import { describe, expect, test } from 'vitest';
import { plainFolderKey } from '../model.ts';
import { win } from './fixtures.ts';

describe('plainFolderKey', () => {
  test('a missing row groups under Other instead of throwing', () => {
    expect(plainFolderKey(null)).toBe('~other');
    expect(plainFolderKey(undefined)).toBe('~other');
  });

  test('a row groups by its own folder, trailing slashes trimmed', () => {
    expect(plainFolderKey(win('w', { cwd: '/repo/app/' }))).toBe('/repo/app');
  });

  test('a row with no cwd groups under Other', () => {
    expect(plainFolderKey(win('w', { cwd: '' }))).toBe('~other');
  });
});
