// CMX-315: a Work payload for the phone suites: two projects, one with a run in progress, a
// PR in review, a rework run with high risk and CI failing, and a queue of Linear issues.
const COLUMNS = [
    { name: 'Backlog', type: 'backlog' }, { name: 'Todo', type: 'unstarted' }, { name: 'In Progress', type: 'started' },
    { name: 'In Review', type: 'started' }, { name: 'Done', type: 'completed' }, { name: 'Canceled', type: 'canceled', hidden: true },
];
const PR = n => `https://github.com/example/chelamux/pull/${n}`;

export const WORK_PAYLOAD = {
    configured: true,
    workflows: [
        {
            path: '/p/chelamux/WORKFLOW.md', project_key: 'CMX', tracker_kind: 'linear', tracker_columns: COLUMNS,
            active_runs: [
                { task_id: 'CMX-162', project_key: 'CMX', task_number: 162, title: 'A killed judge self-check leaves its mutation on disk', status: 'running',
                    branch_name: 'liavacc/cmx-162-a-killed-judge-self-check', pr_url: PR(847), pr_checks: 'pending', tracker_state: 'In Progress', started_at: '2026-10-23T08:00:00Z' },
                { task_id: 'CMX-303', project_key: 'CMX', task_number: 303, title: 'Nightly sweep 1/3: the scheduler (covered window, key store, daemon wiring)', status: 'running',
                    branch_name: 'liavacc/cmx-303-nightly-sweep', pr_url: PR(838), pr_checks: 'failing', rework_count: 1, risk: 'high', tracker_state: 'In Progress',
                    handoff: 'Slice 1/3 of the CMX-299 split. Look first at the window gate in run() (no advance on CANNOT VERIFY, a refused filing or UNRUN) and the Filer seam.',
                    started_at: '2026-10-23T07:00:00Z' },
            ],
            awaiting_review_runs: [
                { task_id: 'CMX-314', project_key: 'CMX', task_number: 314, title: 'Flaky in CI: test_ticklock detached judge', status: 'awaiting_review',
                    branch_name: 'liavacc/cmx-314-flaky', pr_url: PR(848), pr_checks: 'passing', tracker_state: 'In Review', ended_at: '2026-10-23T06:00:00Z' },
            ],
            open_tasks: [
                { id: 'CMX-287', title: 'Agents read their own Linear issue', tracker_state: 'Todo', risk: 'normal' },
                { id: 'CMX-304', title: 'Nightly sweep 2/3: same-shape site finder', tracker_state: 'Todo', risk: 'high' },
                { id: 'CMX-308', title: 'A run whose issue leaves the ready states', tracker_state: 'Todo', risk: 'high' },
                { id: 'CMX-192', title: 'Rework counter counts infra failures', tracker_state: 'Todo', risk: 'normal' },
                { id: 'CMX-309', title: 'Menu item icons render at 24px on desktop', tracker_state: 'Todo', risk: 'normal' },
                { id: 'CMX-310', title: 'Settings modal resizes on tab switch', tracker_state: 'Todo', risk: 'normal' },
            ],
            recent_runs: [],
        },
        {
            path: '/p/lean-alpha/WORKFLOW.md', project_key: 'LAP', tracker_kind: 'linear', tracker_columns: COLUMNS,
            active_runs: [],
            awaiting_review_runs: [
                { task_id: 'LAP-9', project_key: 'LAP', task_number: 9, title: 'Backtest the new universe', status: 'needs_human',
                    pr_url: 'https://github.com/example/lean-alpha/pull/12', pr_checks: 'passing', tracker_state: 'In Review', ended_at: '2026-10-22T06:00:00Z' },
            ],
            open_tasks: [
                { id: 'LAP-10', title: 'Seed the research notebook', tracker_state: 'Todo' },
                { id: 'LAP-11', title: 'Pin the data vendor', tracker_state: 'Todo' },
            ],
            recent_runs: [],
        },
    ],
};
