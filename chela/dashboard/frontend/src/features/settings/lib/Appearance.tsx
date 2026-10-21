// CMX-221 (CMX-212 P15): the Appearance tab: the theme (CMX-381: it re-themes the terminals too)
// and the terminal faces and size. Per browser, applied live to every open pane.
import { useState } from 'react';
import { FieldRow, Select } from '../../../shared/ui/index.ts';
import { TERM_FONT_LABELS, TERM_LATIN_LABELS, TERM_SIZES, THEME_LABELS, optionsOf } from '../model.ts';
import { DEFAULTS, KEYS, setTermPref, setTheme } from './prefs.ts';
import { Section } from './parts.tsx';
import { lsRead } from './storage.ts';

function usePref(key: keyof typeof DEFAULTS) {
  return useState(() => lsRead(KEYS[key]) || DEFAULTS[key]);
}

export function Appearance() {
  const [theme, setThemeState] = usePref('theme');
  const [latin, setLatin] = usePref('latin');
  const [font, setFont] = usePref('font');
  const [size, setSize] = usePref('size');
  return (
    <>
      <Section title="Theme">
        <FieldRow id="theme-select" label="Appearance" keywords="theme dark light colour color mode">
          <Select value={theme} options={optionsOf(THEME_LABELS)} onChange={(t) => { setTheme(t); setThemeState(t); }} />
        </FieldRow>
      </Section>
      <Section title="Terminal font">
        <p className="s-desc">Applies live to every open terminal, saved per browser. Pick the <strong>English</strong>{' '}
          (monospace) and <strong>Hebrew</strong> faces independently. Only <strong>Miriam Mono</strong> keeps Hebrew on the
          grid; the other Hebrew faces are proportional: nicer letters, slight drift in the fixed cells.</p>
        <FieldRow id="term-latin-select" label="English font" keywords="terminal typeface monospace latin">
          <Select value={latin} options={optionsOf(TERM_LATIN_LABELS)} onChange={(v) => { setTermPref('latin', v); setLatin(v); }} />
        </FieldRow>
        <FieldRow id="term-font-select" label="Hebrew font" keywords="terminal typeface rtl">
          <Select value={font} options={optionsOf(TERM_FONT_LABELS)} onChange={(v) => { setTermPref('font', v); setFont(v); }} />
        </FieldRow>
        <FieldRow id="term-size-select" label="Size" keywords="terminal font size zoom px">
          <Select value={size} options={TERM_SIZES.map((s) => ({ value: s, label: `${s}px` }))} onChange={(v) => { setTermPref('size', v); setSize(v); }} />
        </FieldRow>
      </Section>
    </>
  );
}
