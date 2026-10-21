// HOST RESOURCES STRIP: live CPU/RAM/Disk in the sidebar foot (CMX-172), sampled on request by
// the dashboard process itself (GET /api/resources), never the daemon. Ported from
// static/js/resources.js by CMX-219 (CMX-212 P06). A plain poll on the refresh loop: there is
// no push for host resources.
//
// A readout whose value comes back null (an unreadable host) hides itself rather than show a
// stale or fake number; the % text is the signal, its colour (model.ts level()) decoration.
import { api } from '../../../shared/api/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { humanBytes, level } from '../model.ts';

type Sample = Partial<Record<'cpu_pct' | 'mem_pct' | 'mem_used' | 'mem_total' | 'disk_pct' | 'disk_used' | 'disk_total', number | null>>;
type Metric = { id: string; label: string; pct: keyof Sample; used?: keyof Sample; total?: keyof Sample };

const METRICS: Metric[] = [
  { id: 'res-cpu', label: 'CPU', pct: 'cpu_pct' },
  { id: 'res-mem', label: 'RAM', pct: 'mem_pct', used: 'mem_used', total: 'mem_total' },
  { id: 'res-disk', label: 'Disk', pct: 'disk_pct', used: 'disk_used', total: 'disk_total' },
];

/** The last /api/resources sample; null until one lands. */
export const resources = createStore<Sample | null>(null);

/** Never throws out: a transient failure keeps the last sample (the next tick retries). */
export async function tickResources(): Promise<void> {
  try {
    const data = (await api('/api/resources')) as Sample | null;
    if (data) resources.set(data);
  } catch (e) {
    console.error('tickResources', e);
  }
}

function Readout({ m, data }: { m: Metric; data: Sample }) {
  const value = data[m.pct];
  if (value == null) return <span className="res-item" id={m.id} hidden />;
  const used = m.used ? data[m.used] : null;
  const total = m.total ? data[m.total] : null;
  const title = used != null && total != null ? `${m.label}: ${humanBytes(used)} / ${humanBytes(total)}` : `${m.label}: ${Math.round(value)}%`;
  return (
    <span className={`res-item res-${level(value)}`} id={m.id} title={title}>
      <span className="res-label">{m.label}</span><span className="res-value">{Math.round(value)}%</span>
    </span>
  );
}

export function Resources() {
  const data = useStore(resources);
  return (
    <div className="res-strip" id="resources-strip" hidden={!data}>
      {data && METRICS.map((m) => <Readout key={m.id} m={m} data={data} />)}
    </div>
  );
}

registerLane('resources', () => tickResources());
