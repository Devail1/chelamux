// CMX-395: the header's Claude account line, and the per-session account-switch badge.
export {
  ACCOUNT_POLL_MS, ClaudeAccount, accountLane, claudeAccount, resetClaudeAccount, tickClaudeAccount,
} from './lib/ClaudeAccount.tsx';
export { accountLabel, accountView, left, switchBadge, utc, type AccountSwitch, type AccountView } from './model.ts';
