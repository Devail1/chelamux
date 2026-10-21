// The sidebar head's inbox button: the owner's dot (how healthy the orchestrator address
// is) and the unread badge (how many decisions this browser has not looked at), two
// independent signals, both always on. The orchestrator HOLDS the inbox, so with one
// registered a click also jumps to it (CMX-76, nav.js's jumpToOrchestrator), and the log
// still opens over it; with none the click only opens the log.
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { orchestrator } from '../../../shared/store/orchestrator.ts';
import { IconButton } from '../../../shared/ui/index.ts';
import { formatUnreadCount, unreadCount, unreadUrgency } from '../model.ts';
import { chipMeta } from './chip.ts';
import { inbox, openDecisions } from './inbox.ts';

/** nav.js's jump, looked up on `win.chela` at call time (legacy loads after the islands). */
export type JumpToOrchestrator = () => unknown;

export function DecisionsButton({ jump }: { jump: JumpToOrchestrator }) {
  const owner = useStore(orchestrator);
  const s = useStore(inbox);
  const meta = chipMeta(owner.state);
  const count = s.loaded ? unreadCount(s.events, s.lastSeen) : 0;
  const urgency = unreadUrgency(s.events, s.lastSeen);
  return (
    <IconButton
      className="icon-btn"
      id="btn-decisions"
      label={owner.wid ? 'Decisions inbox: jump to the orchestrator' : 'Decisions inbox'}
      aria-haspopup="dialog"
      onClick={() => {
        try {
          jump();
        } catch (e) {
          console.error('jump to the orchestrator', e); // the log still opens
        }
        openDecisions();
      }}
    >
      <icons.inbox aria-hidden />
      <span className={`decisions-dot dot-${meta.cls}`} title={meta.word} />
      {count > 0 && (
        <span className={`decisions-unread${urgency !== 'neutral' ? ` decisions-unread-${urgency}` : ''}`}>{formatUnreadCount(count)}</span>
      )}
    </IconButton>
  );
}
