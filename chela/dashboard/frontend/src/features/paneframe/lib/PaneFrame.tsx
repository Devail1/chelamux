// One pane's terminal: the ttyd <iframe> once its port is assigned, a spinner placeholder until
// then. React owns the element and its src and never renders inside it (FRONTEND_PORT_PLAN.md
// §3). The cid is drawn once per mount, so a repaint never reconnects; a rebuilt tile is a new
// mount and a new connection, as before.
import { useEffect, useState } from 'react';
import { TextButton } from '../../../shared/ui/index.ts';
import { frameSrc, newCid } from '../model.ts';
import { retryReady, startReadyPoll, type Readiness } from './ready.ts';

export interface PaneFrameProps {
  wid: string;
  /** The window's label (the iframe's title, the placeholder's text). */
  title: string;
  ready: Readiness;
  /** Blanked by the background-tab teardown: about:blank, its src kept in data-suspended-src. */
  suspended: boolean;
  onLoad: (ifr: HTMLIFrameElement) => void;
}

export function PaneFrame({ wid, title, ready, suspended, onLoad }: PaneFrameProps) {
  const [cid] = useState(newCid);
  useEffect(() => (ready === 'pending' ? startReadyPoll(wid) : undefined), [wid, ready]);
  if (ready === 'ready') {
    const src = frameSrc(wid, cid);
    return (
      <iframe
        className="term-frame"
        // the Clipboard API, delegated so the in-pane Ctrl+V shim can read the clipboard
        allow="clipboard-read; clipboard-write"
        src={suspended ? 'about:blank' : src}
        data-suspended-src={suspended ? src : undefined}
        title={title}
        onLoad={(e) => onLoad(e.currentTarget)}
      />
    );
  }
  return (
    <div className="term-frame term-pending" data-pending={wid}>
      {ready === 'retry' ? (
        <div className="term-pending-inner">
          still starting.{' '}
          <TextButton className="term-retry" onClick={() => retryReady(wid)}>
            click to retry
          </TextButton>
        </div>
      ) : (
        <div className="term-pending-inner">
          <span className="term-spinner" /> starting {title}…
        </div>
      )}
    </div>
  );
}
