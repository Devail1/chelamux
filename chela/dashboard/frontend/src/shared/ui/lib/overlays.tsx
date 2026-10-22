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
import { useLayoutEffect, useMemo, useRef, type ReactElement, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject, type SyntheticEvent } from 'react';

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

// CMX-306: what runs as any Dialog or Sheet opens. The sidebar registers closing its phone
// drawer here (legacy closeSidebar()), so a modal opened from the drawer never opens behind it.
const openListeners = new Set<() => void>();
/** Run `fn` each time a Dialog or Sheet opens; returns the unsubscribe. */
export function onOverlayOpen(fn: () => void): () => void {
  openListeners.add(fn);
  return () => openListeners.delete(fn);
}

/** Remember the opener as the overlay opens (from the focus history: Radix skips
 * onOpenAutoFocus when an autoFocus field already holds focus) and tell the listeners. */
function useOpening(open: boolean) {
  const opener = useRef<Element | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    opener.current = openerNow();
    for (const fn of openListeners) {
      try {
        fn();
      } catch (e) {
        console.error('an overlay-open listener failed', e); // the overlay opens all the same
      }
    }
  }, [open]);
  return opener;
}

/**
 * A tap on the backdrop closes the overlay. Radix waits for the `click` a touch ends in, and
 * iOS Safari sends none for a tap on an element with no click handler of its own (the legacy
 * overlays carried an inline onclick): this handler is that click target. A floating layer
 * open above the dialog (a Select's list, a menu) takes the tap instead, as Radix's own
 * dismiss does.
 */
const backdropClose = (onOpenChange: (open: boolean) => void) => (e: ReactMouseEvent<HTMLElement>) => {
  if (e.target !== e.currentTarget) return;
  if (document.querySelector('[data-radix-popper-content-wrapper]')) return;
  onOpenChange(false);
};

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
  /** `sheet`: the safety sheets' look (anchored top right, a bottom sheet on a phone), with
   * the title in a header row beside its icon and a close button (CMX-240). `settings`: the
   * Settings modal, its title and close button in a header row. `palette`: the ⌘K palette's
   * box (its title for assistive tech only). `shortcuts`: the shortcuts sheet, over the
   * palette's backdrop, its header row the icon, title, an Esc hint and a close button. */
  variant?: 'modal' | 'sheet' | 'settings' | 'palette' | 'shortcuts';
  icon?: LucideIcon;
  /** The title names the dialog for assistive tech only: the content draws its own heading. */
  hideTitle?: boolean;
  /** The box's id. */
  id?: string;
  /** Escape, before the dialog closes on it: `preventDefault()` keeps it open. */
  onEscapeKeyDown?: (e: KeyboardEvent) => void;
  /** Focus stays where it is as the dialog opens (a field outside it is still typing into it). */
  keepFocus?: boolean;
  children?: ReactNode;
};

const SURFACE = {
  modal: { overlay: 'modal-overlay active', box: 'modal', desc: 'modal-desc' },
  sheet: { overlay: 'shares-backdrop ui-sheet', box: 'shares-sheet', desc: 'ss-sub' },
  settings: { overlay: 'drawer-scrim open', box: 'settings-modal open', desc: 's-desc' },
  palette: { overlay: 'palette-overlay open', box: 'palette', desc: 'palette-desc' },
  shortcuts: { overlay: 'palette-overlay open', box: 'shortcuts-sheet', desc: 'palette-desc' },
} as const;

/** The modal and palette variants' close button, in the box's corner. Drawn after the content,
 * so focus still opens on the content's first field, not on it. */
function CornerClose({ variant }: { variant: keyof typeof SURFACE }) {
  if (variant !== 'modal' && variant !== 'palette') return null;
  return (
    <RD.Close className={cx('task-modal-close ui-close', variant === 'palette' && 'palette-close')} aria-label="Close">
      <X aria-hidden size={18} />
    </RD.Close>
  );
}

/** The title row each variant draws: its heading, plus a close button where it has one (the
 * rest draw CornerClose). Every variant has one: a phone has no Esc (CMX-306). */
function DialogHead({ variant, heading, title, Icon }: { variant: keyof typeof SURFACE; heading: ReactNode; title: string; Icon?: LucideIcon }) {
  if (variant === 'sheet') {
    return (
      <div className="ss-hd">
        {Icon && (
          <span className="ss-hd-ic">
            <Icon aria-hidden size={15} />
          </span>
        )}
        {heading}
        <RD.Close className="ss-close ui-close" aria-label="Close">
          <X aria-hidden size={16} />
        </RD.Close>
      </div>
    );
  }
  if (variant === 'settings') {
    return (
      <div className="settings-modal-head">
        <RD.Title asChild>
          <span>{title}</span>
        </RD.Title>
        <RD.Close className="icon-btn ui-close" aria-label="Close">
          <X aria-hidden size={16} />
        </RD.Close>
      </div>
    );
  }
  if (variant === 'shortcuts') {
    return (
      <div className="shortcuts-head">
        {Icon && (
          <span className="sc-ic">
            <Icon aria-hidden size={16} />
          </span>
        )}
        {heading}
        <span className="sc-esc">close <kbd>Esc</kbd></span>
        <RD.Close className="icon-btn ui-close" aria-label="Close">
          <X aria-hidden size={16} />
        </RD.Close>
      </div>
    );
  }
  return heading;
}

/**
 * A modal dialog: focus moves in, is trapped, and returns to whatever had it when the dialog
 * opened (Radix returns it only to its own Trigger, and a legacy opener is not one); Escape
 * and an outside click close it.
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  className,
  hideTitle,
  variant = 'modal',
  icon: Icon,
  id,
  onEscapeKeyDown,
  keepFocus,
  children,
}: DialogProps) {
  const opener = useOpening(open);
  if (!title.trim()) throw new Error('<Dialog> needs a title: it is the dialog\'s accessible name');
  const surface = SURFACE[variant];
  const heading = (
    <RD.Title asChild>
      <h3 className={hideTitle ? 'sr-only' : undefined}>{title}</h3>
    </RD.Title>
  );
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className={cx(surface.overlay, 'ui-overlay')} onClick={backdropClose(onOpenChange)}>
          <RD.Content
            id={id}
            className={cx(surface.box, className)}
            {...(description ? {} : { 'aria-describedby': undefined })}
            onEscapeKeyDown={onEscapeKeyDown}
            onOpenAutoFocus={keepFocus ? (e) => e.preventDefault() : undefined}
            onCloseAutoFocus={(e) => {
              // A dialog handing over to another (a Decisions row opening its task) needs nothing
              // here: the new dialog's focus trap keeps focus inside it.
              if (opener.current instanceof HTMLElement) {
                e.preventDefault();
                opener.current.focus();
              }
            }}
          >
            <DialogHead variant={variant} heading={heading} title={title} Icon={Icon} />
            {description && <RD.Description className={surface.desc}>{description}</RD.Description>}
            {children}
            <CornerClose variant={variant} />
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
  const opener = useOpening(open);
  if (!title.trim()) throw new Error('<Sheet> needs a title: it is the dialog\'s accessible name');
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="shares-backdrop ui-overlay" onClick={backdropClose(onOpenChange)}>
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
              <RD.Close className="ss-close ui-close" aria-label="Close">
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
  // no Trigger for Radix to return focus to: the last anchor gets it back
  const last = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (anchor) last.current = anchor;
  }, [anchor]);
  return (
    <RP.Root open={!!anchor} onOpenChange={(o) => {
        if (!o) onClose();
      }}>
      <RP.Anchor virtualRef={ref as RefObject<HTMLElement>} />
      <RP.Portal>
        <RP.Content className={cx('popover ui-floating', className)} aria-label={label} side="bottom" align={align} sideOffset={GAP} collisionPadding={MARGIN}
          onFocusOutside={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => {
            if (last.current?.isConnected) {
              e.preventDefault();
              last.current.focus();
            }
          }}
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
      /** Extra classes on the row (the pane menu's rows keep their legacy names). */
      className?: string;
      title?: string;
      /** `data-*` attributes on the row, for code that finds it by them. */
      data?: Record<`data-${string}`, string>;
      /** A press that starts a gesture (the Wire row's drag), before any pick. */
      onMouseDown?: (e: ReactMouseEvent<HTMLElement>) => void;
      /** Read-only content after the label (a mode glyph, a count). */
      trailing?: ReactNode;
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
    <RM.Item className={cx('popover-item ov-item', it.checked && 'on', it.back && 'ov-back', it.className)} disabled={it.disabled} onSelect={onSelect}
      title={it.title} onMouseDown={it.onMouseDown} {...it.data}
      {...(role ? { role, 'aria-checked': !!it.checked } : {})} {...(it.submenu ? { 'aria-haspopup': 'menu' as const } : {})}>
      {it.back && <span className="ov-chev back"><ChevronRight size={12} aria-hidden /></span>}
      {it.icon && (
        <span className="ov-ic">
          <it.icon aria-hidden />
        </span>
      )}
      <span className="ov-label">{it.label}</span>
      {it.value !== undefined && <span className="ov-val">{it.value}</span>}
      {it.trailing}
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

/** One toast; it closes itself after `duration` ms (Radix pauses that while hovered), and on a
 * click when `dismissOnClick`. */
export function Toast({ open, onOpenChange, title, duration = 6000, dismissOnClick, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; duration?: number; dismissOnClick?: boolean; children?: ReactNode }) {
  return (
    <RToast.Root className="run-toast" open={open} onOpenChange={onOpenChange} duration={duration} onClick={dismissOnClick ? () => onOpenChange(false) : undefined}>
      <RToast.Title>{title}</RToast.Title>
      {children && <RToast.Description>{children}</RToast.Description>}
    </RToast.Root>
  );
}
