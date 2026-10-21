// CMX-228 (CMX-212 P08): the Decisions inbox. `model.ts` (unread/search/click-through math)
// and `feed.ts` (a log row's class, the cursor drain) are this module's other entry points.
export { DecisionsButton, type JumpToOrchestrator } from './lib/DecisionsButton.tsx';
export { DecisionsDialog } from './lib/DecisionsDialog.tsx';
export { CHIP_META } from './lib/chip.ts';
export { DECISION_TYPES, LAST_SEEN_KEY, closeDecisions, inbox, inboxOpen, inboxQuery, openDecisionTicket, openDecisions, refreshLog, tickDecisions, type InboxState } from './lib/inbox.ts';
export { legacyJump, mountDecisions } from './lib/mount.tsx';
