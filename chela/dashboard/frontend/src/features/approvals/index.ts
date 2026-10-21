// CMX-219 (CMX-212 P05): pending approvals, the pill and its sheet (CMX-103). `model.ts` is its
// other entry point (legacy Settings imports setApprovalNotify from it).
export { ARM_MS, ApprovalsPill, ApprovalsSheet, openApprovals } from './lib/Approvals.tsx';
export { approvals, resetApprovals, tickApprovals } from './lib/state.ts';
