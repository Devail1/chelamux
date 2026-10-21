// CMX-239/240 (CMX-212 P24, P25): sharing a pane. The share dialog, the active-shares pill and
// the Active shares sheet (its kill switch), the page's share state, and owner presence. Legacy
// terminals.js imports this for the pane header (still legacy until P26).
export { fetchShareOptions, mintShare, type MintResponse, type MintResult } from './lib/api.ts';
export { closeSharesSheet, configureShareHost, followAgents, openSharesSheet, reconcileShares, setShareMode, shareSession, startShares, stopAllShares, stopShare, type ShareHost } from './lib/actions.ts';
export { closeShareDialog, openShareDialog, type OpenShareDialog } from './lib/mount.tsx';
export { mountShares } from './lib/mountShares.tsx';
export { durLabel, fmtShareEnd, isLong, mintBody, pillView, SHARE_MODE_UI, SHARE_NOT_SANDBOXED_REASON, SHARE_TYPING_OFF_REASON, shareOffer, shareTimeLeft, unsafeBannerText, unsafeDurations, upgradeBody, type PillView, type ShareDraft, type ShareMode, type ShareOffer, type ShareOptions } from './lib/model.ts';
export { paneToast } from './lib/paneToast.ts';
export { stopOwnerPresence } from './lib/presence.ts';
export { ShareDialog, type PaneDims, type ShareDialogProps } from './lib/ShareDialog.tsx';
export { ShareInfoRows, type ShareInfo } from './lib/ShareInfoRows.tsx';
export { SharesPill } from './lib/SharesPill.tsx';
export { SharesSheet } from './lib/SharesSheet.tsx';
export { applyShared, asMode, dropShare, isShared, markShared, noteShare, seedFromAgents, shareMode, shares, type ShareAgent, type SharedMap, type ShareEntry, type Shares } from './lib/state.ts';
