// CMX-221 (CMX-212 P17): the Timing tab: the daemon and dispatcher cadences (CMX-217), the first
// knob group on the dashboard-setting precedence layer (env wins over the dashboard).
import { KnobRows } from './Knobs.tsx';
import { Section } from './parts.tsx';

export function Timing() {
  return (
    <Section title="Timing" id="settings-timing" keywords="interval cadence tick poll seconds daemon">
      <KnobRows path="/api/config/timing" kind="timing">
        <p className="s-desc">Daemon and dispatcher cadences. Blank a field to fall back to its <code>CHELA_*</code> env var
          (or the built-in default, shown as its placeholder). Takes effect on the <strong>next tick</strong>, no restart,
          except the status-feed timeout/TTL pair (marked below), which the dashboard/daemon process reads once at startup. A
          knob whose env var is currently set is disabled here. <strong>env always wins</strong>, so an edit would be
          silently discarded.</p>
      </KnobRows>
    </Section>
  );
}
