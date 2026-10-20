// Settings > Concurrency (CMX-164): the pure half of the "Agents at once" / "Judges at
// once" steppers. No DOM, so tests/concurrency_model.test.mjs can pin it directly.
import { humanBytes } from './resourcesmodel';

// The POST body for one stepper click. delta 0 is "Reset" (clear the override); any
// other delta steps the effective value, clamped to the server's bounds.
export function stepPayload(state, kind, idx, delta) {
    const cur = kind === 'judges' ? state.judges.effective : state.workflows[idx].effective;
    const value = delta === 0 ? '' : Math.min(state.max, Math.max(state.min, cur + delta));
    return kind === 'judges' ? { judges: value } : { workflow: state.workflows[idx].path, value };
}

// What N at once means for memory: N x the measured judge peak, next to what is free.
export function memNote(state, n) {
    const free = state.available_bytes == null ? 'unknown'
        : `${humanBytes(state.available_bytes)} (${state.available_source})`;
    return `Memory: up to ${n} × ${humanBytes(state.judge_peak_bytes)} judge peak = `
        + `${humanBytes(n * state.judge_peak_bytes)}. Available now: ${free}.`;
}

export function workflowNote(state, w) {
    const src = w.override == null ? 'From WORKFLOW.md' : `Override (WORKFLOW.md says ${w.file_value})`;
    return `${src}. ${memNote(state, w.effective)}`;
}
