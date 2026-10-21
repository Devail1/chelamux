// CMX-217 (CMX-212 P03): boots the legacy page (it was static/js/main.js). The imports keep
// main.js's order: the legacy graph has import cycles, and a module evaluated first would see
// the others' `let`s in their temporal dead zone. Each feature registers its own refresh lane
// and SSE handlers at import (shared/scheduler, shared/events), so this file only starts them.
import { REFRESH_MS } from '../../static/js/util';
import { renderNav, selectView } from '../../static/js/nav';
import { startWorkPoll } from '../../static/js/work';
import { VIEWS } from '../../static/js/views';
import { findView } from '../../static/js/viewreg';
import '../../static/js/decisions';
import { BOOT } from './shared/api/index.ts';
import { openEvents } from './shared/events/index.ts';
import { refresh, setViewTicks, startScheduler } from './shared/scheduler/index.ts';

setViewTicks((id) => findView(VIEWS, id)?.tick ?? null);
renderNav(); // the sidebar's view list, from the registry, before anything selects one
// The Wall is the default canvas when terminals are on; selectView runs the boot tick.
if (BOOT.terminalsEnabled) selectView('terminals');
else void refresh();
startScheduler(REFRESH_MS);
// The ONE /api/dispatcher poll (board, runs, the always-visible sidebar badges): app-level,
// not view-level, since the badges are on screen from every view.
startWorkPoll();
openEvents();
