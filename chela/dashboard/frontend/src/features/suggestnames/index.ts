// CMX-434: the sidebar's "Suggest names" dialog. `model.ts` (the pure rows, checks and apply)
// is this module's other entry point.
export { SuggestNames, closeSuggestNames, openSuggestNames, retitle } from './lib/SuggestNames.tsx';
export { TITLE_MAX, titleNote, type TitleOutcome } from './model.ts';
