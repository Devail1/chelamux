// CMX-218 (CMX-212 P04): the form kit. Every ported form lays out through <FieldRow>, one
// grid `label | control | unit` whose label track is FIXED (style.css .field-row), so every
// control in a section starts at the same x however long its label, and a unit sits right
// after its control. A ported component renders no raw <input>/<select>/<button> outside ui/
// (FRONTEND_PORT_PLAN.md §4 rule 7).
import * as RS from '@radix-ui/react-select';
import * as RSw from '@radix-ui/react-switch';
import { createContext, useContext, useId, useState, type ReactElement, type ReactNode } from 'react';
import { icons } from '../../icons/index.ts';
import { Tooltip } from './overlays.tsx';

/** The id FieldRow's <label> points at; a control inside one takes it. */
const FieldId = createContext<string | undefined>(undefined);

export function FieldRow({ label, hint, unit, badge, children }: { label: string; hint?: ReactNode; unit?: string; badge?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <div className="field-row">
      <label className="field-label" htmlFor={id}>
        {label}
        {badge}
      </label>
      <div className="field-control">
        <FieldId.Provider value={id}>{children}</FieldId.Provider>
      </div>
      <span className="field-unit">{unit}</span>
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

/** Why a control is read-only: its env var is set, and env wins over the dashboard. */
function envTip(env: string) {
  return `Set by ${env}; env wins, unset it to edit here`;
}

type InputProps<T> = {
  value: T;
  onChange: (value: T) => void;
  /** The env var that pins this knob. Set ⇒ the control is disabled and says why. */
  lockedBy?: string;
  disabled?: boolean;
  label?: string;
};

function withEnvTip(lockedBy: string | undefined, el: ReactElement) {
  return lockedBy ? <Tooltip content={envTip(lockedBy)}><span className="field-locked" tabIndex={0}>{el}</span></Tooltip> : el;
}

/** `list` names a <datalist> of suggestions; `autoFocus` takes focus when a Dialog opens. */
export function TextInput({ value, onChange, lockedBy, disabled, label, placeholder, list, autoFocus }: InputProps<string> & { placeholder?: string; list?: string; autoFocus?: boolean }) {
  const id = useContext(FieldId);
  return withEnvTip(
    lockedBy,
    <input className="s-input" type="text" id={id} aria-label={label} value={value} placeholder={placeholder} list={list} autoFocus={autoFocus} autoComplete={list ? 'off' : undefined} disabled={disabled || !!lockedBy} onChange={(e) => onChange(e.target.value)} />,
  );
}

/** Multi-line text (a brief, a prompt): it takes the row's full width. */
export function TextArea({ value, onChange, disabled, label, rows = 3 }: InputProps<string> & { rows?: number }) {
  const id = useContext(FieldId);
  return <textarea className="s-input s-textarea" id={id} aria-label={label} rows={rows} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
}

/** Pick any number of options (ctrl/⌘-click), as a list box. */
export function MultiSelect({ value, onChange, options, disabled, label, size = 4 }: InputProps<string[]> & { options: { value: string; label: string }[]; size?: number }) {
  const id = useContext(FieldId);
  return (
    <select className="s-select s-multi" id={id} aria-label={label} multiple size={size} value={value} disabled={disabled}
      onChange={(e) => onChange([...e.target.selectedOptions].map((o) => o.value))}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/**
 * A number knob. Empty means "the default": `fallback` shows as the placeholder, and the value
 * is null. What is typed stays as typed until it parses (`-`, `0.` mid-entry), so a half-typed
 * number is never wiped by the controlled value.
 */
export function NumberInput({ value, onChange, lockedBy, disabled, label, fallback, min, max }: InputProps<number | null> & { fallback?: number; min?: number; max?: number }) {
  const id = useContext(FieldId);
  const [draft, setDraft] = useState<string | null>(null);
  return withEnvTip(
    lockedBy,
    <input
      className="s-input s-num"
      type="number"
      inputMode="decimal"
      id={id}
      aria-label={label}
      value={draft ?? value ?? ''}
      placeholder={fallback === undefined ? undefined : String(fallback)}
      min={min}
      max={max}
      disabled={disabled || !!lockedBy}
      onBlur={() => setDraft(null)}
      onChange={(e) => {
        const t = e.target;
        setDraft(t.value);
        if (t.value === '' && !t.validity.badInput) onChange(null);
        else if (!Number.isNaN(t.valueAsNumber)) onChange(t.valueAsNumber);
      }}
    />,
  );
}

export function Select({ value, onChange, options, lockedBy, disabled, label }: InputProps<string> & { options: { value: string; label: string }[] }) {
  const id = useContext(FieldId);
  return withEnvTip(
    lockedBy,
    <RS.Root value={value} onValueChange={onChange} disabled={disabled || !!lockedBy}>
      <RS.Trigger className="s-select ui-select" id={id} aria-label={label}>
        <RS.Value />
        <RS.Icon asChild>
          <icons.chevronDown aria-hidden size={14} />
        </RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content className="popover ui-floating" position="popper" sideOffset={4} collisionPadding={8}>
          <RS.Viewport>
            {options.map((o) => (
              <RS.Item key={o.value} value={o.value} className="popover-item">
                <RS.ItemText>{o.label}</RS.ItemText>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>,
  );
}

/** An on/off switch (role="switch", Space toggles). */
export function Toggle({ value, onChange, lockedBy, disabled, label }: InputProps<boolean>) {
  const id = useContext(FieldId);
  return withEnvTip(
    lockedBy,
    <RSw.Root className="ui-toggle" id={id} aria-label={label} checked={value} onCheckedChange={onChange} disabled={disabled || !!lockedBy}>
      <RSw.Thumb className="ui-toggle-thumb" />
    </RSw.Root>,
  );
}
