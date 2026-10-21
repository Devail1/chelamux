import { test, expect, mock } from 'claude-code/testing'
import { NEEDS_YOU } from './needsyou-cases'
import { age, dashboardPort, parseItems } from './needsyou'

const NOW = NEEDS_YOU.now_ms
const ORCH = { CHELA_WID: '@7', CHELA_ORCHESTRATOR_WID: '@7' }
const BODY = JSON.stringify(NEEDS_YOU.response)
const LINE = 'needs you: 1 approval · 1 access request · 1 run in needs_human · oldest: access request mount /data (read-only) (20m)'

// The world beneath the mod. `fetch` answers each GET /api/needs-you (a function, so a test
// can change it between polls); every URL asked is recorded.
function world(on: any, env: Record<string, string>,
               o: { fetch?: () => any, base?: string, inbox?: string } = {}) {
  const w = { urls: [] as string[], fetch: o.fetch ?? (() => ({ status: 200, ok: true, headers: {}, text: BODY })) }
  mock.env(on, env)
  on('session.version', () => ({ value: { version: o.base ?? '2.1.302', base: o.base ?? '2.1.302' } }))
  on('session.usage', () => ({ value: { context: { window: 200000 }, rateLimits: [] } }))
  for (const [k, v] of [['id', 's-1'], ['cwd', '/repo'], ['model', 'm']]) on(`session.${k}`, () => ({ value: v }))
  on('fs.read', () => { if (o.inbox === undefined) throw new Error('ENOENT'); return { value: o.inbox } })
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '' } }))
  on('http.fetch', async (_: any, e: any) => { w.urls.push(e.url); return { value: await w.fetch() } })
  on('session.start', () => ({ cwd: '/repo' }))
  // the engine's own band, which the needs-you line must sit under, never replace
  on('ui.render', ($: any, e: any) => {
    const { Box, Text } = $.ui.resolve(e)
    return h(Box, { key: 'engine' }, h(Text, {}, 'ENGINE'))
  })
  return w
}

async function started($: any, on: any, env: Record<string, string>, o: any = {}) {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, env, o)
  await $.session.start({ source: 'startup', cwd: '/repo', surface: 'terminal' } as any)
  await clock.settle()
  return { w, clock }
}

async function bandText($: any, surface: 'terminal' | 'desktop' = 'terminal', hasSurvey = false) {
  const ui = await $.ui.mount({ plugin: 'chela-hooks-mod', surface, component: 'AbovePrompt',
                                props: { hasSurvey, isWorking: false } as any })
  const line = await ui.find({ key: 'needs-you' })
  const links = await ui.findAll({ type: 'Link' })
  await ui.unmount()
  return { text: line?.text, links: links.map((l: any) => [l.props.label, l.props.href]) }
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: N pending, the band shows the counts and the oldest, each linked`, async ($, on) => {
    const { w } = await started($, on, ORCH)
    expect(w.urls).toEqual(['http://127.0.0.1:5001/api/needs-you'])
    expect(await bandText($, surface)).toEqual({ text: LINE, links: [
      ['1 approval', 'https://chela.example/override/ov-1'],
      ['1 access request', 'https://chela.example/'],
      ['1 run in needs_human', 'https://chela.example/'],
      ['access request mount /data (read-only)', 'https://chela.example/'],
    ] })
  })
}

test('the band sits under what is beneath it, never in place of it', async ($, on) => {
  await started($, on, ORCH)
  const ui = await $.ui.mount({ plugin: 'chela-hooks-mod', surface: 'terminal', component: 'AbovePrompt',
                                props: { hasSurvey: false, isWorking: false } as any })
  expect((await ui.findAll({ type: 'Text' })).map((t: any) => t.text)[0]).toBe('ENGINE')
  await ui.unmount()
})

test('plural counts; the oldest is the smallest `since`, not the first listed', async ($, on) => {
  const items = [
    { kind: 'approval', label: 'CMX-1', since: NOW / 1000 - 120, url: 'https://c/override/a' },
    { kind: 'approval', label: 'CMX-2', since: NOW / 1000 - 7200, url: 'https://c/override/b' },
    { kind: 'run', label: 'CMX-3', since: NOW / 1000 - 60, url: 'https://c/' },
    { kind: 'run', label: 'CMX-4', since: NOW / 1000 - 30, url: 'https://c/' },
  ]
  await started($, on, ORCH, { fetch: () => ({ status: 200, ok: true, headers: {}, text: JSON.stringify({ items }) }) })
  expect(await bandText($)).toEqual({
    text: 'needs you: 2 approvals · 2 runs in needs_human · oldest: approval CMX-2 (2h)',
    links: [['2 approvals', 'https://c/override/b'], ['2 runs in needs_human', 'https://c/'],
            ['approval CMX-2', 'https://c/override/b']] })
})

test('0 pending: no band', async ($, on) => {
  await started($, on, ORCH, { fetch: () => ({ status: 200, ok: true, headers: {}, text: '{"items": []}' }) })
  expect(await bandText($)).toEqual({ text: undefined, links: [] })
})

for (const [name, env, inbox] of [
  ['another window is the orchestrator', { CHELA_WID: '@8', CHELA_ORCHESTRATOR_WID: '@7' }, undefined],
  ['inbox.json names another window', { CHELA_WID: '@8', CHELA_DIR: '/c' }, '{"orchestrator": "@7"}'],
  ['not a chela window', { CHELA_ORCHESTRATOR_WID: '@7' }, undefined],
  ['no orchestrator recorded', { CHELA_WID: '@7' }, undefined],
] as const) {
  test(`a non-orchestrator session (${name}): no band, and the daemon is never asked`, async ($, on) => {
    const { w } = await started($, on, env as any, { inbox })
    expect(w.urls).toEqual([])
    expect((await bandText($)).text).toBeUndefined()
  })
}

test('the orchestrator recorded in inbox.json gets the band', async ($, on) => {
  await started($, on, { CHELA_WID: '@7', CHELA_DIR: '/c' }, { inbox: '{"orchestrator": "@7"}' })
  expect((await bandText($)).text).toBe(LINE)
})

test('below Claude Code 2.1.301: no band, and the daemon is never asked', async ($, on) => {
  const { w } = await started($, on, ORCH, { base: '2.1.300' })
  expect([w.urls, (await bandText($)).text]).toEqual([[], undefined])
})

for (const [name, fetch] of [
  ['the fetch rejects (daemon down)', () => { throw new Error('ECONNREFUSED') }],
  ['a 503 (a source unreadable)', () => ({ status: 503, ok: false, headers: {}, text: BODY })],
  ['a body that is not JSON', () => ({ status: 200, ok: true, headers: {}, text: '<html>' })],
  ['no items list', () => ({ status: 200, ok: true, headers: {}, text: '{"error": "x"}' })],
] as const) {
  test(`a fetch failure, ${name}: no band`, async ($, on) => {
    await started($, on, ORCH, { fetch })
    expect((await bandText($)).text).toBeUndefined()
  })
}

test('a failure after a good reading clears the band at the next poll', async ($, on) => {
  const { w, clock } = await started($, on, ORCH)
  expect((await bandText($)).text).toBe(LINE)
  w.fetch = () => { throw new Error('ECONNREFUSED') }
  await clock.advance(10000)
  expect(w.urls.length).toBe(2)
  expect((await bandText($)).text).toBeUndefined()
})

test('polls every 10 s, and a new reading moves the band', async ($, on) => {
  const { w, clock } = await started($, on, ORCH)
  await clock.advance(9999)
  expect(w.urls.length).toBe(1)
  w.fetch = () => ({ status: 200, ok: true, headers: {}, text: '{"items": []}' })
  await clock.advance(1)
  expect(w.urls.length).toBe(2)
  expect((await bandText($)).text).toBeUndefined()
})

test('data older than 30 s is hidden, even with no poll to replace it', async ($, on) => {
  // The daemon answers the first poll, then hangs: no reading replaces the first one.
  let calls = 0
  const { clock } = await started($, on, ORCH, { fetch: () => {
    calls += 1
    return calls === 1 ? { status: 200, ok: true, headers: {}, text: BODY } : new Promise(() => {})
  } })
  expect((await bandText($)).text).toBe(LINE)
  await clock.advance(30000)
  expect((await bandText($)).text).toBe(LINE)
  await clock.advance(1)
  expect((await bandText($)).text).toBeUndefined()
  // a hung poll is not stacked: one call in flight, not one per tick
  expect(calls).toBe(2)
})

test('a band already on screen goes once its reading is stale, with no new draw asked', async ($, on) => {
  // The daemon answers once, then hangs. Nothing redraws a mounted band but a state change,
  // so only the tick clearing the stale reading takes it off the screen.
  let calls = 0
  const { clock } = await started($, on, ORCH, { fetch: () => {
    calls += 1
    return calls === 1 ? { status: 200, ok: true, headers: {}, text: BODY } : new Promise(() => {})
  } })
  const ui = await $.ui.mount({ plugin: 'chela-hooks-mod', surface: 'terminal', component: 'AbovePrompt',
                                props: { hasSurvey: false, isWorking: false } as any })
  expect((await ui.find({ key: 'needs-you' }))?.text).toBe(LINE)
  await clock.advance(30000)
  expect((await ui.find({ key: 'needs-you' }))?.text).toBe(LINE)
  await clock.advance(10000)
  expect(await ui.find({ key: 'needs-you' })).toBeUndefined()
  await ui.unmount()
})

test('after a reload, a poll the old timer left waiting never writes', async ($, on) => {
  // The first poll hangs until the test lets it answer; the reload's poll says "nothing".
  const held: ((v: any) => void)[] = []
  const { clock, w } = await started($, on, ORCH, { fetch: () => new Promise(r => { held.push(r) }) })
  w.fetch = () => ({ status: 200, ok: true, headers: {}, text: '{"items": []}' })
  await $.session.start({ source: 'startup', cwd: '/repo', surface: 'terminal' } as any)
  await clock.settle()
  expect(w.urls.length).toBe(2)
  held[0]!({ status: 200, ok: true, headers: {}, text: BODY })
  await clock.settle()
  expect((await bandText($)).text).toBeUndefined()
})

test('the band is drawn only while the reading is at most 30 s old', async ($, on) => {
  // The draw-time check on its own: a clock whose timer never fires, so no tick clears
  // the first reading and only its age decides.
  let t: number = NOW
  on('clock.now', () => ({ value: t }))
  on('clock.every', () => new Promise(() => {}))
  world(on, ORCH)
  await $.session.start({ source: 'startup', cwd: '/repo', surface: 'terminal' } as any)
  expect((await bandText($)).text).toBe(LINE)
  t = NOW + 30000
  expect((await bandText($)).text).toBe(LINE)
  t = NOW + 30001
  expect((await bandText($)).text).toBeUndefined()
})

test('the band yields to a survey', async ($, on) => {
  await started($, on, ORCH)
  expect((await bandText($, 'terminal', true)).text).toBeUndefined()
})

test('CHELA_DASHBOARD_PORT picks the port the daemon is asked on', async ($, on) => {
  const { w } = await started($, on, { ...ORCH, CHELA_DASHBOARD_PORT: '5005' })
  expect(w.urls).toEqual(['http://127.0.0.1:5005/api/needs-you'])
})

test('the port reads as chela.config.dashboard_port reads CHELA_DASHBOARD_PORT', () => {
  for (const [raw, port] of [[undefined, '5001'], ['', '5001'], ['5005', '5005'], [' 6000\n', '6000'],
                             ['50x', '5001'], ['x5005', '5001'], ['-1', '5001']] as const)
    expect([raw, dashboardPort(raw)]).toEqual([raw, port])
})

test('only the documented shape parses; anything else is unknown, never "none"', () => {
  expect(parseItems(BODY)).toEqual(NEEDS_YOU.response.items)
  expect(parseItems('{"items": []}')).toEqual([])
  for (const bad of ['', 'null', '{}', '{"items": {}}', '{"items": [null]}',
                     '{"items": [{"kind": "other", "label": "x", "since": 1, "url": "u"}]}',
                     '{"items": [{"kind": "run", "label": 1, "since": 1, "url": "u"}]}',
                     '{"items": [{"kind": "run", "label": "x", "since": "1", "url": "u"}]}',
                     '{"items": [{"kind": "run", "label": "x", "since": 1, "url": 2}]}',
                     '{"items": [{"kind": "run", "label": "x", "since": 1e999, "url": "u"}]}'])
    expect([bad, parseItems(bad)]).toEqual([bad, null])
})

test('ages: minutes under an hour, hours under a day, then days', () => {
  for (const [s, out] of [[0, '0m'], [59, '0m'], [60, '1m'], [3599, '59m'], [3600, '1h'],
                          [86399, '23h'], [86400, '1d'], [-5, '0m']] as const)
    expect([s, age(s)]).toEqual([s, out])
})
