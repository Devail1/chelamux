import { act } from 'react';
import { expect, test } from 'vitest';
import { mount } from './main.tsx';

test('mount renders the entry into the element it is given', async () => {
  const el = document.createElement('div');
  await act(async () => mount(el));
  expect(el.textContent).toBe('Hello, world!');
});
