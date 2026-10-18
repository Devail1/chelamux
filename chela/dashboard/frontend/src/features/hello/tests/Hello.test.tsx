import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { Hello } from '../index.ts';

afterEach(cleanup);

test('greets the name it is given', () => {
  render(<Hello name="Ada" />);
  expect(screen.getByText('Hello, Ada!')).toBeTruthy();
});

test('greets the world when no name is given', () => {
  render(<Hello />);
  expect(screen.getByText('Hello, world!')).toBeTruthy();
});
