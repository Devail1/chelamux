// CMX-239 (CMX-212 P24): the share dialog, the share's copy rows and the pane toast. Legacy
// terminals.js imports this; the Active shares sheet moves here in P25.
export { fetchShareOptions, mintShare, type MintResponse, type MintResult } from './lib/api.ts';
export { closeShareDialog, openShareDialog, type OpenShareDialog } from './lib/mount.tsx';
export { durLabel, isLong, mintBody, SHARE_NOT_SANDBOXED_REASON, SHARE_TYPING_OFF_REASON, shareOffer, unsafeDurations, type ShareDraft, type ShareMode, type ShareOffer, type ShareOptions } from './lib/model.ts';
export { paneToast } from './lib/paneToast.ts';
export { ShareDialog, type PaneDims, type ShareDialogProps } from './lib/ShareDialog.tsx';
export { renderShareInfoRows, ShareInfoRows, type ShareInfo } from './lib/ShareInfoRows.tsx';
