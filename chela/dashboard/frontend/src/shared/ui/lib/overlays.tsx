// CMX-218 (CMX-212 P04): the overlay half of the kit. Behaviour (focus trap and return,
// Escape, outside click, keyboard, ARIA, collision-aware placement) is Radix Primitives'; the
// look is style.css's existing classes (.modal-overlay/.modal, .popover/.popover-item,
// .tap-tip, .run-toast-stack/.run-toast), so a ported surface looks like the legacy one.
import * as RD from '@radix-ui/react-dialog';
import * as RM from '@radix-ui/react-dropdown-menu';
import * as RP from '@radix-ui/react-popover';
import * as RToast from '@radix-ui/react-toast';
import * as RT from '@radix-ui/react-tooltip';
import type { LucideIcon } from 'lucide-react';
import { useRef, type ReactElement, type ReactNode } from 'react';

const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

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
  children?: ReactNode;
};

/**
 * A modal dialog: focus moves in, is trapped, and returns to whatever had it when the dialog
 * opened (Radix returns it only to its own Trigger, and a legacy opener is not one); Escape
 * and an outside click close it.
 */
export function Dialog({ open, onOpenChange, title, description, className, children }: DialogProps) {
  const opener = useRef<Element | null>(null);
  if (!title.trim()) throw new Error('<Dialog> needs a title: it is the dialog\'s accessible name');
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="modal-overlay active">
          <RD.Content
            className={cx('modal', className)}
            {...(description ? {} : { 'aria-describedby': undefined })}
            onOpenAutoFocus={() => {
              opener.current = document.activeElement;
            }}
            onCloseAutoFocus={(e) => {
              if (opener.current instanceof HTMLElement && opener.current.isConnected) {
                e.preventDefault();
                opener.current.focus();
              }
            }}
          >
            <RD.Title asChild>
              <h3>{title}</h3>
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

export type MenuItem =
  | { label: string; onSelect: () => void; icon?: LucideIcon; disabled?: boolean }
  | 'separator';

/** A dropdown menu, named by its trigger: arrow keys move, Enter picks, typing jumps, Escape closes. */
export function Menu({ trigger, items, className }: { trigger: ReactElement; items: MenuItem[]; className?: string }) {
  return (
    <RM.Root>
      <RM.Trigger asChild>{trigger}</RM.Trigger>
      <RM.Portal>
        <RM.Content className={cx('popover overflow-menu ui-floating', className)} align="end" sideOffset={GAP} collisionPadding={MARGIN}>
          {items.map((it, i) =>
            it === 'separator' ? (
              <RM.Separator key={i} className="popover-sep" />
            ) : (
              <RM.Item key={i} className="popover-item ov-item" disabled={it.disabled} onSelect={it.onSelect}>
                {it.icon && (
                  <span className="ov-ic">
                    <it.icon aria-hidden />
                  </span>
                )}
                <span>{it.label}</span>
              </RM.Item>
            ),
          )}
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

/** One toast; it closes itself after `duration` ms (Radix pauses that while hovered). */
export function Toast({ open, onOpenChange, title, duration = 6000, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; duration?: number; children?: ReactNode }) {
  return (
    <RToast.Root className="run-toast" open={open} onOpenChange={onOpenChange} duration={duration}>
      <RToast.Title>{title}</RToast.Title>
      {children && <RToast.Description>{children}</RToast.Description>}
    </RToast.Root>
  );
}
