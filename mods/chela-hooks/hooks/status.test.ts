import { test, expect, mock } from 'claude-code/testing'
import { STATUS } from './status-cases'
import { atFloor, bar } from './status'

const ENV = { CHELA_WID: '@7', CHELA_DIR: '/c', CHELA_MOD_STATUSLINE: '/cache.sh' }
const LINE = 'ctx ▰▰▰▰▱▱▱▱▱▱ 42% · @7'

// The engine beneath the mod, recording every `$.ui.status` text, file read and process run.
function world(on: any, env: Record<string, string>, o: { usage?: any, base?: string,
                                                           inbox?: string, runFails?: boolean } = {}) {
  const w = { status: [] as (string | undefined)[], runs: [] as any[], reads: [] as string[] }
  mock.env(on, env)
  on('session.version', () => ({ value: { version: o.base ?? '2.1.302', base: o.base ?? '2.1.302' } }))
  on('session.usage', () => ({ value: o.usage ?? STATUS.usage }))
  for (const [k, v] of [['id', STATUS.session_id], ['cwd', STATUS.cwd], ['model', STATUS.model]])
    on(`session.${k}`, () => ({ value: v }))
  on('fs.read', (_: any, e: any) => {
    w.reads.push(e.path); if (o.inbox === undefined) throw new Error('ENOENT'); return { value: o.inbox } })
  on('ui.status', (_: any, e: any) => { w.status.push(e.text); return { value: undefined } })
  on('process.run', (_: any, e: any) => {
    w.runs.push(e); if (o.runFails) throw new Error('ENOENT'); return { value: { exitCode: 0, stdout: '', stderr: '' } } })
  on('turn.complete', () => ({ text: 'x' }))
  on('session.start', () => ({ cwd: STATUS.cwd }))
  on('session.compact', () => ({ skip: 'test' }))
  return w
}
// Raise `turn.complete` (or `fire` another event) and let the unawaited redraw settle.
async function turn($: any, on: any, w0: () => any, fire?: () => Promise<unknown>) {
  const clock = mock.clock(on)
  const w = w0()
  await (fire ? fire() : $.turn.complete({ reason: 'answer', answer: 'x', durationMs: 1,
                                            isAborted: false, turnId: 't-1' }))
  await clock.settle()
  return w
}

test('the line has every field, and the cache gets the statusLine payload', async ($, on) => {
  const w = await turn($, on, () => world(on, { ...ENV, CHELA_ORCHESTRATOR_WID: '@7',
                                                CHELA_RUN_ID: 'CMX-9', CHELA_RUN_ROLE: 'judge' }))
  expect(w.status).toEqual([`${LINE} · orchestrator · run CMX-9 (judge)`])
  expect(w.runs).toEqual([{ argv: ['bash', '/cache.sh'], init: { stdin: expect.any(String), timeoutMs: 10000 } }])
  expect(JSON.parse(w.runs[0].init.stdin)).toEqual(STATUS.payload)
})

test('an absent field is left out, never "undefined"', async ($, on) => {
  const w = await turn($, on, () => world(on, { CHELA_DIR: '/c', CHELA_MOD_STATUSLINE: '/cache.sh' },
    { usage: { ...STATUS.usage, context: { window: 200000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 5 }],
               cost: undefined }, inbox: '{"orchestrator": "@7"}' }))
  expect([w.status, w.reads]).toEqual([[undefined], []])
  const p = JSON.parse(w.runs[0].init.stdin)
  expect([p.context_window, p.rate_limits, p.cost]).toEqual([{ context_window_size: 200000 },
    { five_hour: { used_percentage: 5 } }, undefined])
})

test('a run with no role; inbox.json names another orchestrator', async ($, on) => {
  const w = await turn($, on, () => world(on, { ...ENV, CHELA_RUN_ID: 'CMX-9' }, { inbox: '{"orchestrator": "@3"}' }))
  expect(w.status).toEqual([`${LINE} · run CMX-9`])
  expect(w.reads).toEqual(['/c/inbox.json'])
})

test('the orchestrator recorded in inbox.json is marked at start; $CHELA_INBOX_FILE wins', async ($, on) => {
  const w = await turn($, on, () => world(on, { ...ENV, CHELA_INBOX_FILE: '/i.json' },
    { inbox: '{"orchestrator": "@7"}' }), () => $.session.start({ source: 'startup' } as any))
  expect(w.status).toEqual([`${LINE} · orchestrator`])
  expect(w.reads).toEqual(['/i.json'])
})

test('an unreadable inbox.json is no orchestrator; HOME/.chela by default', async ($, on) => {
  const w = await turn($, on, () => world(on, { CHELA_WID: '@7', HOME: '/h', CHELA_MOD_STATUSLINE: '/x' },
    { inbox: '{nope' }))
  expect(w.status).toEqual([LINE])
  expect(w.reads).toEqual(['/h/.chela/inbox.json'])
})

test('redrawn after a compaction', async ($, on) => {
  const w = await turn($, on, () => world(on, ENV), () =>
    $.session.compact({ trigger: 'manual', messages: [{ role: 'user', text: 'x', toolUses: [] }] } as any))
  expect(w.status).toEqual([LINE])
})

test('the cache script defaults to the repo one; a failed write never fails the turn', async ($, on) => {
  const w = await turn($, on, () => world(on, { CHELA_WID: '@7' }, { runFails: true }))
  expect(w.runs[0].argv[1]).toMatch(/^\/.*\/mods\/chela-hooks\/\.\.\/\.\.\/scripts\/cache-statusline\.sh$/)
  expect(w.status).toEqual([LINE])
})

test('nothing renders and nothing is cached when disabled', { options: { statusLine: false } }, async ($, on) => {
  const w = await turn($, on, () => world(on, ENV))
  expect([w.status, w.runs]).toEqual([[undefined], []])
})

test('nothing renders below the version floor', async ($, on) => {
  const w = await turn($, on, () => world(on, ENV, { base: '2.1.300' }))
  expect([w.status, w.runs]).toEqual([[undefined], []])
})

test('the floor is 2.1.301, an unreadable version fails closed; the bar is ten cells', () => {
  for (const v of ['2.1.301', '2.1.302', '2.2.0', '3.0.0', '2.1.301-dev']) expect(atFloor(v)).toBe(true)
  for (const v of ['2.1.300', '2.0.999', '1.9.400', '', 'x2.1.301', undefined]) expect(atFloor(v)).toBe(false)
  expect([bar(0), bar(42), bar(100), bar(130), bar(-5)]).toEqual(['▱▱▱▱▱▱▱▱▱▱', '▰▰▰▰▱▱▱▱▱▱',
    '▰▰▰▰▰▰▰▰▰▰', '▰▰▰▰▰▰▰▰▰▰', '▱▱▱▱▱▱▱▱▱▱'])
})
