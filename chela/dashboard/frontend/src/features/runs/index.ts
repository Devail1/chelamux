// CMX-231 (CMX-212 P10): the Runs segment of WORK and the Init-a-repo dialog. `model.ts` (run
// state → look) and `legacy.ts` (string forms for kanban.js / taskmodal.js) are entry points too.
export { InitRepo, initReport, launcherPaths, openInitRepo } from './lib/InitRepo.tsx';
export { Runs } from './lib/Runs.tsx';
export { postWorkDelete, type DeletePayload } from './lib/delete.ts';
