// CMX-240 (CMX-212 P25): the active-shares pill, the kill switch's way in (CMX-377/422). Two
// copies: #btn-shares in .safety-float on every tab and breakpoint, and #term-shares in the
// Wall's toolbar row (style.css hides the floating one while the in-row one shows). Hidden
// while nothing is shared, so a forgotten share, an exposed live terminal, is never unseen.
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { IconButton } from '../../../shared/ui/index.ts';
import { openSharesSheet } from './actions.ts';
import { useNow } from './clock.ts';
import { pillView } from './model.ts';
import { shares } from './state.ts';

const PILL_ICON = { view: icons.viewOnly, typing: icons.typing, unsandboxed: icons.unsandboxed } as const;

export function SharesPill({ id }: { id: string }) {
  const v = pillView([...useStore(shares).values()], useNow());
  const Icon = PILL_ICON[v.mode];
  return (
    <IconButton id={id} className={v.mode === 'unsandboxed' ? 'shares-indicator si-unsandboxed' : 'shares-indicator'} data-mode={v.mode} hidden={v.count === 0} label={v.count ? v.label : 'Active shares'} onClick={() => openSharesSheet()}>
      <span className="si-dot" aria-hidden="true" />
      <Icon aria-hidden size={13} />
      <span className="si-text">{v.text}</span>
    </IconButton>
  );
}
