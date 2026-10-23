// CMX-252's "Pause all agents", behind a confirmation step (CMX-315 on the phone, CMX-316 on
// the desktop): Cancel posts nothing, the danger button posts `--pause --all`.
import { Button, Dialog, DialogClose } from '../../../shared/ui/index.ts';
import { PAUSE_ALL_ASK, type Hold } from './hold.ts';

export function PauseAllConfirm({ open, onOpenChange, h }: { open: boolean; onOpenChange: (open: boolean) => void; h: Hold }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Pause all agents?" description={PAUSE_ALL_ASK} className="work-confirm">
      <div className="work-confirm-actions">
        <DialogClose asChild>
          <Button>Cancel</Button>
        </DialogClose>
        <Button variant="danger" id="wopt-pause-all-confirm" disabled={h.busy}
          onClick={() => {
            onOpenChange(false);
            h.pauseAll();
          }}>
          Pause all agents
        </Button>
      </div>
    </Dialog>
  );
}
