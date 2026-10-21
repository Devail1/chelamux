// CMX-218 (CMX-212 P04): buttons, badges, section headers and tabs, as style.css's classes.
import * as RTabs from '@radix-ui/react-tabs';
import { useState, type ButtonHTMLAttributes, type ReactNode, type Ref } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md';

/** The class each variant and size draws with. */
export const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: 'btn btn-primary',
  secondary: 'btn',
  danger: 'btn btn-danger',
  ghost: 'btn btn-ghost',
};
const BUTTON_SIZE: Record<ButtonSize, string> = { sm: 'btn-sm', md: 'btn-md' };

export function Button({ variant = 'secondary', size = 'md', className, type = 'button', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize }) {
  return <button type={type} className={[BUTTON_VARIANT[variant], BUTTON_SIZE[size], className].filter(Boolean).join(' ')} {...rest} />;
}

/**
 * An icon-only control, drawn by its own `className` (no .btn look): `label` is its accessible
 * name and tooltip. It takes a ref, so a Menu or Popover can use it as its trigger.
 */
export function IconButton({ label, type = 'button', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; ref?: Ref<HTMLButtonElement> }) {
  return <button type={type} title={label} aria-label={label} {...rest} />;
}

/**
 * A row of a popover list (.popover-item): a real button, so the keyboard reaches it, drawn as
 * the legacy menus' rows. Its text is its name; `className` adds to the row's classes.
 */
export function ItemButton({ className, type = 'button', ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={['popover-item', className].filter(Boolean).join(' ')} {...rest} />;
}

/**
 * A text control drawn by its own `className` (a filter pill, a segment, a card's action), not
 * the .btn look. `aria-pressed` makes it a toggle.
 */
export function TextButton({ type = 'button', ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} {...rest} />;
}

/**
 * A chip's tone, by meaning: ok (ci green), warn (RESTART, awaiting), bad (failed, off),
 * info (rework N), alert (needs a human), muted (done). Each is an existing .badge-* class.
 */
export type BadgeTone = 'ok' | 'warn' | 'bad' | 'info' | 'alert' | 'muted';
export const BADGE_TONE: Record<BadgeTone, string> = {
  ok: 'badge-on',
  warn: 'badge-awaiting',
  bad: 'badge-off',
  info: 'badge-rework',
  alert: 'badge-needs-human',
  muted: 'badge-done',
};

export function Badge({ tone = 'muted', title, children }: { tone?: BadgeTone; title?: string; children: ReactNode }) {
  return (
    <span className={`badge ${BADGE_TONE[tone]}`} title={title}>
      {children}
    </span>
  );
}

/** A section's heading (the Settings cards' accent-barred h4), with optional actions at its right. */
export function SectionHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="section-header">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

export type Tab = { value: string; label: string; content: ReactNode; className?: string };

/** The tab strip's classes, for a surface whose look predates the kit (the task modal's). */
export type TabsClasses = { list?: string; tab?: string };

/** Tabs: arrow keys move between them (Radix); the active tab carries `.active` as legacy did. */
export function Tabs({ tabs, value, onValueChange, label, classes }: { tabs: Tab[]; value?: string; onValueChange?: (v: string) => void; label: string; classes?: TabsClasses }) {
  const [own, setOwn] = useState(tabs[0]?.value ?? '');
  // A tab that left the list falls back to the first, never a blank panel.
  const wanted = value ?? own;
  const current = tabs.some((t) => t.value === wanted) ? wanted : (tabs[0]?.value ?? '');
  const change = (v: string) => {
    setOwn(v);
    onValueChange?.(v);
  };
  const tab = classes?.tab ?? 'tab';
  return (
    <RTabs.Root value={current} onValueChange={change}>
      <RTabs.List className={classes?.list ?? 'tabs'} aria-label={label}>
        {tabs.map((t) => (
          <RTabs.Trigger key={t.value} value={t.value} className={t.value === current ? `${tab} active` : tab}>
            {t.label}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {tabs.map((t) => (
        <RTabs.Content key={t.value} value={t.value} className={t.className}>
          {t.content}
        </RTabs.Content>
      ))}
    </RTabs.Root>
  );
}
