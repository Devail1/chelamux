// CMX-218: the form kit. FieldRow's alignment is the point (Settings > Timing had each input
// at a different x, after its label), so it is checked against style.css itself.
import { act, fireEvent, render, screen } from '@testing-library/react';
import STYLE_CSS from '../../../../../static/style.css?raw';
import { useState } from 'react';
import { afterAll, beforeAll, expect, test, vi } from 'vitest';
import { BADGE_TONE, BUTTON_VARIANT, Badge, Button, FieldRow, NumberInput, RadioGroup, SectionHeader, Select, Tabs, TextInput, Toggle, useMediaQuery } from '../index.ts';
import { must } from './setup.ts';

let sheet: HTMLStyleElement;
beforeAll(() => {
  sheet = document.createElement('style');
  sheet.textContent = STYLE_CSS;
  document.head.append(sheet);
});
afterAll(() => sheet.remove());

const css = (el: Element) => getComputedStyle(el);

test('FieldRow puts label, control and unit in grid tracks 1, 2, 3', () => {
  const { container } = render(
    <FieldRow label="Poll interval" unit="s" hint="How often">
      <NumberInput value={30} onChange={() => {}} />
    </FieldRow>,
  );
  const row = must(container.querySelector('.field-row'));
  expect(css(row).display).toBe('grid');
  const label = must(row.querySelector('label'));
  const input = screen.getByRole('spinbutton');
  expect(label.textContent).toBe('Poll interval');
  expect(label.htmlFor).toBe(input.id);
  expect(input.id).not.toBe('');
  expect(css(label).gridColumn).toBe('1');
  expect(css(must(input.parentElement)).gridColumn).toBe('2');
  expect(css(must(row.querySelector('.field-unit'))).gridColumn).toBe('3');
  expect(must(row.querySelector('.field-unit')).textContent).toBe('s');
  expect(must(row.querySelector('.field-hint')).textContent).toBe('How often');
});

test('two FieldRows with very different labels start their controls at the same x', () => {
  const { container } = render(
    <>
      <FieldRow label="TTL">
        <NumberInput value={1} onChange={() => {}} />
      </FieldRow>
      <FieldRow label="Seconds an idle agent waits before the dispatcher reclaims its slot">
        <NumberInput value={2} onChange={() => {}} />
      </FieldRow>
    </>,
  );
  const [a, b] = [...container.querySelectorAll('.field-row')];
  const tracks = [css(must(a)).gridTemplateColumns, css(must(b)).gridTemplateColumns];
  // The label track is a fixed length: a content-sized one (auto, max-content, fr) moves the
  // control with the label's length.
  expect(tracks[0]).toMatch(/^\d+(\.\d+)?(rem|px|em) /);
  expect(tracks[1]).toBe(tracks[0]);
  for (const row of [must(a), must(b)]) expect(css(must(row.querySelector('.field-control'))).gridColumn).toBe('2');
});

test('NumberInput: the default shows as the placeholder; empty is null, a number a number', () => {
  const onChange = vi.fn();
  render(<NumberInput label="Poll" value={null} fallback={30} onChange={onChange} />);
  const input = screen.getByRole('spinbutton', { name: 'Poll' }) as HTMLInputElement;
  expect([input.value, input.placeholder, input.className]).toEqual(['', '30', 's-input s-num']);
  fireEvent.change(input, { target: { value: '45' } });
  expect(onChange.mock.calls).toEqual([[45]]);
});

test('NumberInput: clearing a set value reports null (back to the default)', () => {
  const onChange = vi.fn();
  render(<NumberInput label="Poll" value={45} fallback={30} onChange={onChange} />);
  const input = screen.getByRole('spinbutton') as HTMLInputElement;
  expect(input.value).toBe('45');
  fireEvent.change(input, { target: { value: '' } });
  expect(onChange.mock.calls).toEqual([[null]]);
});

test('NumberInput keeps a half-typed number instead of wiping it, and reports nothing yet', () => {
  const onChange = vi.fn();
  render(<NumberInput label="Delta" value={null} onChange={onChange} />);
  const input = screen.getByRole('spinbutton') as HTMLInputElement;
  // A browser reports "-" (mid-way to "-5") as value '' with validity.badInput.
  Object.defineProperty(input, 'validity', { value: { badInput: true } });
  fireEvent.change(input, { target: { value: '' } });
  expect(onChange).not.toHaveBeenCalled();
});

test('NumberInput shows what is typed until blur, then the value it was given', () => {
  const onChange = vi.fn();
  render(<NumberInput label="Poll" value={5} onChange={onChange} />);
  const input = screen.getByRole('spinbutton') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '7' } });
  expect(onChange.mock.calls).toEqual([[7]]);
  expect(input.value).toBe('7');
  fireEvent.blur(input);
  expect(input.value).toBe('5');
});

test('a floating box opens above the modal overlay (a Select inside a Dialog)', () => {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay active';
  const box = document.createElement('div');
  box.className = 'popover ui-floating';
  document.body.append(overlay, box);
  expect(Number(css(box).zIndex)).toBeGreaterThan(Number(css(overlay).zIndex));
  overlay.remove();
  box.remove();
});

test('NumberInput without a default has no placeholder', () => {
  render(<NumberInput label="n" value={3} onChange={() => {}} />);
  expect((screen.getByRole('spinbutton') as HTMLInputElement).hasAttribute('placeholder')).toBe(false);
});

test('a control locked by an env var is disabled and says which var, on focus', async () => {
  render(<TextInput label="Projects" value="~/p" lockedBy="CHELA_PROJECTS" onChange={() => {}} />);
  const input = screen.getByRole('textbox', { name: 'Projects' }) as HTMLInputElement;
  expect(input.disabled).toBe(true);
  fireEvent.focus(must(input.parentElement));
  await act(async () => {});
  expect(screen.getByRole('tooltip').textContent).toBe('Set by CHELA_PROJECTS; env wins, unset it to edit here');
});

test('an env-locked NumberInput and Select are disabled too', () => {
  render(
    <>
      <NumberInput label="Poll" value={3} lockedBy="CHELA_POLL" onChange={() => {}} />
      <Select label="Model" value="a" options={[{ value: 'a', label: 'Alpha' }]} lockedBy="CHELA_MODEL" onChange={() => {}} />
    </>,
  );
  expect((screen.getByRole('spinbutton', { name: 'Poll' }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('combobox', { name: 'Model' }) as HTMLButtonElement).disabled).toBe(true);
});

test('an unlocked TextInput is editable and reports its text', () => {
  const onChange = vi.fn();
  render(<TextInput label="Name" value="" onChange={onChange} />);
  const input = screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement;
  expect(input.disabled).toBe(false);
  expect(input.parentElement?.className).not.toBe('field-locked');
  fireEvent.change(input, { target: { value: 'liav' } });
  expect(onChange).toHaveBeenCalledWith('liav');
});

test('Toggle is a switch that reports the flipped value; locked, it does not flip', () => {
  const onChange = vi.fn();
  const { rerender } = render(<Toggle label="Mute" value={false} onChange={onChange} />);
  const sw = screen.getByRole('switch', { name: 'Mute' });
  expect(sw.className).toBe('ui-toggle');
  fireEvent.click(sw);
  expect(onChange.mock.calls).toEqual([[true]]);
  rerender(<Toggle label="Mute" value={false} onChange={onChange} lockedBy="CHELA_MUTE" />);
  fireEvent.click(screen.getByRole('switch'));
  expect(onChange).toHaveBeenCalledTimes(1);
});

test('Select shows the chosen option on an .s-select and picks another', () => {
  const onChange = vi.fn();
  const opts = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Beta' },
  ];
  render(
    <FieldRow label="Model">
      <Select value="a" options={opts} onChange={onChange} />
    </FieldRow>,
  );
  const trigger = screen.getByRole('combobox', { name: 'Model' });
  expect(trigger.className).toBe('s-select ui-select');
  expect(trigger.textContent).toBe('Alpha');
  fireEvent.keyDown(trigger, { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: 'Beta' }));
  expect(onChange).toHaveBeenCalledWith('b');
});

test('Button variants and sizes map to their classes', () => {
  expect(BUTTON_VARIANT).toEqual({ primary: 'btn btn-primary', secondary: 'btn', danger: 'btn btn-danger', ghost: 'btn btn-ghost' });
  render(
    <>
      <Button>Plain</Button>
      <Button variant="primary" size="sm" className="x">
        Go
      </Button>
      <Button variant="danger" type="submit">
        Kill
      </Button>
    </>,
  );
  expect(screen.getByText('Plain').className).toBe('btn btn-md');
  expect(screen.getByText('Plain').getAttribute('type')).toBe('button');
  expect(screen.getByText('Go').className).toBe('btn btn-primary btn-sm x');
  expect(screen.getByText('Kill').className).toBe('btn btn-danger btn-md');
  expect(screen.getByText('Kill').getAttribute('type')).toBe('submit');
});

test('Badge tones map to the existing .badge-* classes', () => {
  expect(BADGE_TONE).toEqual({ ok: 'badge-on', warn: 'badge-awaiting', bad: 'badge-off', info: 'badge-rework', alert: 'badge-needs-human', muted: 'badge-done' });
  render(
    <>
      <Badge tone="warn" title="restart needed">
        restart
      </Badge>
      <Badge>done</Badge>
    </>,
  );
  expect(screen.getByText('restart').className).toBe('badge badge-awaiting');
  expect(screen.getByText('restart').title).toBe('restart needed');
  expect(screen.getByText('done').className).toBe('badge badge-done');
});

test('SectionHeader is an h4 in a .section-header', () => {
  render(<SectionHeader title="Timing">{<Badge tone="info">2</Badge>}</SectionHeader>);
  const h = screen.getByRole('heading', { level: 4, name: 'Timing' });
  expect(h.parentElement?.className).toBe('section-header');
  expect(h.parentElement?.textContent).toBe('Timing2');
});

function TabsHarness({ onChange }: { onChange?: (v: string) => void }) {
  const [v, setV] = useState('a');
  return (
    <Tabs
      label="Settings"
      value={v}
      onValueChange={(n) => {
        setV(n);
        onChange?.(n);
      }}
      tabs={[
        { value: 'a', label: 'General', content: 'general body' },
        { value: 'b', label: 'Timing', content: 'timing body' },
      ]}
    />
  );
}

test('Tabs: the active one carries .active, a click switches panel and reports it', () => {
  const onChange = vi.fn();
  render(<TabsHarness onChange={onChange} />);
  expect(screen.getByRole('tablist', { name: 'Settings' }).className).toBe('tabs');
  const [general, timing] = screen.getAllByRole('tab');
  expect([must(general).className, must(timing).className]).toEqual(['tab active', 'tab']);
  expect(screen.getByRole('tabpanel').textContent).toBe('general body');
  fireEvent.mouseDown(must(timing), { button: 0 });
  expect(onChange).toHaveBeenCalledWith('b');
  expect([must(general).className, must(timing).className]).toEqual(['tab', 'tab active']);
  expect(screen.getByRole('tabpanel').textContent).toBe('timing body');
});

test('uncontrolled Tabs start on the first and switch on their own', () => {
  render(<Tabs label="t" tabs={[{ value: 'x', label: 'X', content: 'xx' }, { value: 'y', label: 'Y', content: 'yy' }]} />);
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Y' }), { button: 0 });
  expect(screen.getByRole('tabpanel').textContent).toBe('yy');
});

test('a controlled tab value that left the list falls back to the first tab', () => {
  render(<Tabs label="t" value="gone" tabs={[{ value: 'x', label: 'X', content: 'xx' }, { value: 'y', label: 'Y', content: 'yy' }]} />);
  expect(screen.getByRole('tabpanel').textContent).toBe('xx');
});

test('an uncontrolled tab removed from the list falls back to the first tab', () => {
  const tabs = [{ value: 'x', label: 'X', content: 'xx' }, { value: 'y', label: 'Y', content: 'yy' }];
  const { rerender } = render(<Tabs label="t" tabs={tabs} />);
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Y' }), { button: 0 });
  rerender(<Tabs label="t" tabs={tabs.slice(0, 1)} />);
  expect(screen.getByRole('tab', { name: 'X' }).className).toBe('tab active');
  expect(screen.getByRole('tabpanel').textContent).toBe('xx');
});

function Probe({ q }: { q: string }) {
  return <span>{useMediaQuery(q) ? 'yes' : 'no'}</span>;
}

test('useMediaQuery answers the query and re-renders when the answer changes', () => {
  let matches = false;
  const listeners = new Set<() => void>();
  const matchMedia = vi.fn((q: string) => ({
    get matches() {
      return q === '(max-width: 768px)' && matches;
    },
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
  }));
  vi.stubGlobal('matchMedia', matchMedia);
  const { unmount } = render(<Probe q="(max-width: 768px)" />);
  expect(screen.getByText('no')).toBeTruthy();
  matches = true;
  act(() => listeners.forEach((f) => f()));
  expect(screen.getByText('yes')).toBeTruthy();
  unmount();
  expect(listeners.size).toBe(0);
  vi.unstubAllGlobals();
});

test('RadioGroup: a labelled group; clicking an option (or its row) picks it, a disabled one never', () => {
  const onChange = vi.fn();
  render(
    <RadioGroup
      label="Access"
      value="a"
      onChange={onChange}
      options={[
        { value: 'a', label: 'Alpha', className: 'x-opt' },
        { value: 'b', label: 'Beta', hint: <span className="why">because</span> },
        { value: 'c', label: 'Gamma', disabled: true },
      ]}
    />,
  );
  expect(screen.getByRole('radiogroup', { name: 'Access' })).toBeTruthy();
  const [a, b, c] = screen.getAllByRole('radio') as HTMLButtonElement[];
  expect([a, b, c].map((r) => must(r).getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
  expect(must(a).closest('label')?.className).toBe('ui-radio-opt x-opt');
  expect(must(c).closest('label')?.className).toBe('ui-radio-opt ui-radio-disabled');
  expect(screen.getByRole('radio', { name: /Beta/ }).closest('label')?.querySelector('.why')?.textContent).toBe('because');
  fireEvent.click(must(b));
  fireEvent.click(must(c));
  expect([c?.disabled, onChange.mock.calls]).toEqual([true, [['b']]]);
  fireEvent.click(must(must(b).closest('label')?.querySelector('.ui-radio-text')));
  expect(onChange.mock.calls).toEqual([['b'], ['b']]);
});

test('TextInput plain: no autocomplete, no spellcheck (a typed confirmation); default keeps both', () => {
  render(
    <>
      <TextInput label="plain" plain value="" onChange={() => {}} />
      <TextInput label="free" value="" onChange={() => {}} />
    </>,
  );
  const [plain, free] = [screen.getByRole('textbox', { name: 'plain' }), screen.getByRole('textbox', { name: 'free' })];
  expect([plain.getAttribute('autocomplete'), plain.getAttribute('spellcheck')]).toEqual(['off', 'false']);
  expect([free.getAttribute('autocomplete'), free.getAttribute('spellcheck')]).toEqual([null, null]);
});
