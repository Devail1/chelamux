// CMX-239: legacy terminals.js opens the dialog by import. It renders into one root of its own,
// made on first use (Radix portals the dialog to <body> anyway), and SYNCHRONOUSLY, so the
// dialog is in the DOM when openShareDialog resolves.
import type { ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { fetchShareOptions, type MintResponse } from './api.ts';
import { ShareDialog, type PaneDims } from './ShareDialog.tsx';

let root: Root | null = null;
let host: HTMLElement | null = null;
/** Bumped by every open and close: an open whose options read was overtaken never renders. */
let gen = 0;

function paint(node: ReactNode) {
  if (!host?.isConnected) {
    root?.unmount();
    host = document.createElement('div');
    host.id = 'share-dialog-root';
    document.body.append(host);
    root = createRoot(host);
  }
  const r = root as Root;
  flushSync(() => r.render(node));
}

export type OpenShareDialog = {
  wid: string;
  title: string;
  dims?: () => PaneDims | null;
  onMinted: (resp: MintResponse) => void;
};

export async function openShareDialog({ wid, title, dims, onMinted }: OpenShareDialog): Promise<void> {
  const mine = ++gen;
  const options = await fetchShareOptions(wid);
  if (mine !== gen) return;
  // This dialog closes only itself: a late close never takes down a newer dialog.
  const close = () => {
    if (mine === gen) closeShareDialog();
  };
  paint(<ShareDialog key={mine} wid={wid} title={title} options={options} dims={dims} onClose={close} onMinted={onMinted} />);
}

export function closeShareDialog(): void {
  gen++;
  if (host?.isConnected) paint(null);
}
