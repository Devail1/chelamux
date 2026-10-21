// CMX-218 (CMX-212 P04): the overlay half of the kit. Behaviour (focus trap and return,
// Escape, outside click, keyboard, ARIA, collision-aware placement) is Radix Primitives'; the
// look is style.css's existing classes (.modal-overlay/.modal, .popover/.popover-item,
// .tap-tip, .run-toast-stack/.run-toast), so a ported surface looks like the legacy one.
import * as RD from '@radix-ui/react-dialog';
import * as RM from '@radix-ui/react-dropdown-menu';
import * as RP from '@radix-ui/react-popover';
import * as RToast from '@radix-ui/react-toast';
import * as RT from '@radix-ui/react-tooltip';
import { Check, ChevronRight, X, type LucideIcon } from 'lucide-react';
import { useLayoutEffect, useMemo, useRef, type ReactElement, type ReactNode, type RefObject, type SyntheticEvent } from 'react';

const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

// The last element focused outside every dialog. As a dialog opens, a field with autoFocus
// inside it may already hold focus, and a dialog opened from another (a Decisions row opening
// its task) finds focus inside the one closing: neither is the control to return to.
let lastOutside: Element | null = null;
const inDialog = (el: Element | null) => !!el?.closest('[role="dialog"]');
if (typeof document !== 'undefined') {
  document.addEventListener(
    'focus', // captured at the document: `focus` does not bubble, but every one passes through here
    (e) => {
      if (e.target instanceof Element && !inDialog(e.target)) lastOutside = e.target;
    },
    true,
  );
}
function openerNow(): Element | null {
  const active = document.activeElement;
  return active && active !== document.body && !inDialog(active) ? active : lastOutside;
}

/** Gap and viewport margin, the legacy popover's (shared/popover). */
const GAP = 6;
const MARGIN = 8;

export type DialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The dialog's accessible name, shown as its heading. Required: a dialog without one throws. */
  title: string;
  description?: ReactNode;
  /** Extra class on the .modal box (e.g. `modal-newtask` for its width). */
  className?: string;
  /** The title names the dialog for assistive tech only: the content draws its own heading. */
  hideTitle?: boolean;
  children?: ReactNode;
};

/**
 * A modal dialog: focus moves in, is trapped, and returns to whatever had it when the dialog
 * opened (Radix returns it only to its own Trigger, and a legacy opener is not one); Escape
 * and an outside click close it.
 */
export function Dialog({ open, onOpenChange, title, description, className, hideTitle, children }: DialogProps) {
  const opener = useRef<Element | null>(null);
  // Remembered as it opens. Radix skips onOpenAutoFocus when an autoFocus field already holds
  // focus, so the opener is read here, from the focus history, not there.
  useLayoutEffect(() => {
    if (open) opener.current = openerNow();
  }, [open]);
  if (!title.trim()) throw new Error('<Dialog> needs a title: it is the dialog\'s accessible name');
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="modal-overlay active">
          <RD.Content
            className={cx('modal', className)}
            {...(description ? {} : { 'aria-describedby': undefined })}
            onCloseAutoFocus={(e) => {
              // A dialog handing over to another (a Decisions row opening its task) needs nothing
              // here: the new dialog's focus trap keeps focus inside it.
              if (opener.current instanceof HTMLElement) {
                e.preventDefault();
                opener.current.focus();
              }
            }}
          >
            <RD.Title asChild>
              <h3 className={hideTitle ? 'sr-only' : undefined}>{title}</h3>
            </RD.Title>
            {description && <RD.Description className="modal-desc">{description}</RD.Description>}
            {children}
          </RD.Content>
        </RD.Overlay>
      </RD.Portal>
    </RD.Root>
  );
}

/** Wrap a button in it to close the enclosing Dialog. */
export const DialogClose = RD.Close;

/**
 * A Dialog in the safety sheets' look (.shares-backdrop / .shares-sheet, the approvals and
 * access-request sheets): its title in the .ss-hd row beside a close button, then `children`.
 * Same behaviour as Dialog (focus trap and return, Escape, outside click).
 */
export function Sheet({ open, onOpenChange, title, className, children }: Omit<DialogProps, 'description' | 'hideTitle'>) {
  const opener = useRef<Element | null>(null);
  // remembered as it opens, as Dialog does
  useLayoutEffect(() => {
    if (open) opener.current = openerNow();
  }, [open]);
  if (!title.trim()) throw new Error('<Sheet> needs a title: it is the dialog\'s accessible name');
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="shares-backdrop">
          <RD.Content className={cx('shares-sheet', className)} aria-describedby={undefined}
            onCloseAutoFocus={(e) => {
              if (opener.current instanceof HTMLElement && opener.current.isConnected) {
                e.preventDefault();
                opener.current.focus();
              }
            }}>
            <div className="ss-hd">
              <RD.Title asChild>
                <span>{title}</span>
              </RD.Title>
              <RD.Close className="ss-close" aria-label="Close">
                <X size={14} aria-hidden />
              </RD.Close>
            </div>
            {children}
          </RD.Content>
        </RD.Overlay>
      </RD.Portal>
    </RD.Root>
  );
}

export type PopoverProps = {
  /** The control it anchors to: one element that takes a ref (a Button, an IconButton). */
  trigger: ReactElement;
  children: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  align?: 'start' | 'center' | 'end';
  className?: string;
  label?: string;
};

/** Anchored below its trigger, flipped above when the viewport has no room, 8px inside it. */
export function Popover({ trigger, children, open, onOpenChange, align = 'end', className, label }: PopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Trigger asChild>{trigger}</RP.Trigger>
      <RP.Portal>
        <RP.Content className={cx('popover ui-floating', className)} aria-label={label} side="bottom" align={align} sideOffset={GAP} collisionPadding={MARGIN}>
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  );
}

export type AnchoredPopoverProps = Omit<PopoverProps, 'trigger' | 'open' | 'onOpenChange'> & {
  /** The element it hangs off; null = closed. One popover can open from several controls. */
  anchor: HTMLElement | null;
  onClose: () => void;
};

/**
 * A Popover with no trigger of its own: it opens at whichever element `anchor` names (the
 * launch menu opens from the sidebar button, the phone "+" and the gear menu's New...).
 * An outside click or Escape closes it; focus leaving it does not (the control that opened
 * it may take focus back after the popover appears).
 */
export function AnchoredPopover({ anchor, onClose, children, align = 'end', className, label }: AnchoredPopoverProps) {
  // Radix measures a virtual anchor through a ref-shaped object
  const ref = useMemo(() => ({ current: anchor }), [anchor]);
  return (
    <RP.Root open={!!anchor} onOpenChange={(o) => {
        if (!o) onClose();
      }}>
      <RP.Anchor virtualRef={ref as RefObject<HTMLElement>} />
      <RP.Portal>
        <RP.Content className={cx('popover ui-floating', className)} aria-label={label} side="bottom" align={align} sideOffset={GAP} collisionPadding={MARGIN}
          onFocusOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => {
            // a click on the control that opened it reopens it there, not close-then-open
            if (anchor && e.target instanceof Node && anchor.contains(e.target)) e.preventDefault();
          }}>
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  );
}

export type MenuItem =
  | {
      label: string;
      onSelect: () => void;
      icon?: LucideIcon;
      disabled?: boolean;
      /** The menu stays open after this pick (a page change, a multi-select, a toggle). */
      keepOpen?: boolean;
      /** A choice: drawn with a check when on, announced as a radio or a checkbox. */
      check?: 'radio' | 'checkbox';
      checked?: boolean;
      /** A submenu's row: its current value at the right, then a chevron. */
      value?: string;
      submenu?: boolean;
      /** A page's back row: a chevron pointing back. */
      back?: boolean;
    }
  | 'separator';

type Entry = Exclude<MenuItem, 'separator'>;

// A page change swaps the items under the focused one: focus the new page's first item, so
// the keyboard keeps its place in the menu.
function refocus(content: Element | null) {
  setTimeout(() => {
    if (!content?.isConnected || content.contains(document.activeElement)) return;
    content.querySelector<HTMLElement>('[role^="menuitem"]:not([data-disabled])')?.focus();
  }, 0);
}

function MenuEntry({ it }: { it: Entry }) {
  const onSelect = (e: Event) => {
    if (it.keepOpen) {
      e.preventDefault();
      refocus((e.currentTarget as Element | null)?.closest('[role="menu"]') ?? null);
    }
    it.onSelect();
  };
  const role = it.check === 'radio' ? 'menuitemradio' : it.check === 'checkbox' ? 'menuitemcheckbox' : undefined;
  return (
    <RM.Item className={cx('popover-item ov-item', it.checked && 'on', it.back && 'ov-back')} disabled={it.disabled} onSelect={onSelect}
      {...(role ? { role, 'aria-checked': !!it.checked } : {})} {...(it.submenu ? { 'aria-haspopup': 'menu' as const } : {})}>
      {it.back && <span className="ov-chev back"><ChevronRight size={12} aria-hidden /></span>}
      {it.icon && (
        <span className="ov-ic">
          <it.icon aria-hidden />
        </span>
      )}
      <span className="ov-label">{it.label}</span>
      {it.value !== undefined && <span className="ov-val">{it.value}</span>}
      {it.checked && <span className="ov-check"><Check size={12} aria-hidden /></span>}
      {it.submenu && <span className="ov-chev"><ChevronRight size={12} aria-hidden /></span>}
    </RM.Item>
  );
}

export type MenuProps = {
  trigger: ReactElement;
  items: MenuItem[];
  className?: string;
  /** Controlled open state, for a menu that also opens another way (a right-click). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Read-only content under the items (the gear menu's usage readouts). */
  footer?: ReactNode;
};

// A portal still bubbles React events up the React tree: a pick inside the menu must not
// also reach whatever holds the trigger (a row that opens on click, a header that folds).
// A right-click inside it opens no native menu over it either.
const contain = (e: SyntheticEvent) => e.stopPropagation();
const containContext = (e: SyntheticEvent) => {
  e.preventDefault();
  e.stopPropagation();
};

/** A dropdown menu, named by its trigger: arrow keys move, Enter picks, typing jumps, Escape closes. */
export function Menu({ trigger, items, className, open, onOpenChange, footer }: MenuProps) {
  // A trigger with an id of its own keeps it (Radix's would be overwritten), so name the menu by it.
  const triggerId = (trigger.props as { id?: string }).id;
  return (
    <RM.Root open={open} onOpenChange={onOpenChange}>
      <RM.Trigger asChild>{trigger}</RM.Trigger>
      <RM.Portal>
        <RM.Content className={cx('popover overflow-menu ui-floating', className)} align="end" sideOffset={GAP} collisionPadding={MARGIN}
          onClick={contain} onContextMenu={containContext} {...(triggerId ? { 'aria-labelledby': triggerId } : {})}>
          {items.map((it, i) =>
            // keyed by label too: a new page's items are new nodes, so refocus() sees focus leave
            it === 'separator' ? <RM.Separator key={i} className="popover-sep" /> : <MenuEntry key={`${i}:${it.label}`} it={it} />,
          )}
          {footer}
        </RM.Content>
      </RM.Portal>
    </RM.Root>
  );
}

/** A hover/focus tooltip, styled as the touch tap-tip. */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactElement }) {
  return (
    <RT.Provider delayDuration={400}>
      <RT.Root>
        <RT.Trigger asChild>{children}</RT.Trigger>
        <RT.Portal>
          <RT.Content className="tap-tip ui-floating" side="top" sideOffset={GAP} collisionPadding={MARGIN}>
            {content}
          </RT.Content>
        </RT.Portal>
      </RT.Root>
    </RT.Provider>
  );
}

/** Render once around the toasts: the bottom-right stack every Toast lands in. */
export function ToastProvider({ children }: { children: ReactNode }) {
  return (
    <RToast.Provider swipeDirection="right">
      {children}
      <RToast.Viewport className="run-toast-stack" />
    </RToast.Provider>
  );
}

/** One toast; it closes itself after `duration` ms (Radix pauses that while hovered), and on
 * a click anywhere on it (a link inside it opens without closing it). */
export function Toast({ open, onOpenChange, title, duration = 6000, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: ReactNode; duration?: number; children?: ReactNode }) {
  return (
    <RToast.Root className="run-toast" open={open} onOpenChange={onOpenChange} duration={duration}
      onClick={(e) => {
        if (!(e.target instanceof Element && e.target.closest('a'))) onOpenChange(false);
      }}>
      <RToast.Title>{title}</RToast.Title>
      {children && <RToast.Description>{children}</RToast.Description>}
    </RToast.Root>
  );
}
