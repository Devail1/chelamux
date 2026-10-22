// One pane's terminal: the ttyd <iframe> once its port is up, a spinner until then. React owns
// the element and its src, never what is inside it (plan §3). The cid is drawn once per mount,
// so a repaint never reconnects.
import { useEffect, useState } from 'react';
import { TextButton } from '../../../shared/ui/index.ts';
import { frameSrc, newCid } from '../model.ts';
import { retryReady, startReadyPoll, type Readiness } from './ready.ts';

export interface PaneFrameProps {
  wid: string;
  title: string; // the window's label
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
      <div className="term-pending-inner">
        {ready === 'retry' ? (
          <>still starting. <TextButton className="term-retry" onClick={() => retryReady(wid)}>click to retry</TextButton></>
        ) : (
          <><span className="term-spinner" /> starting {title}…</>
        )}
      </div>
    </div>
  );
}
