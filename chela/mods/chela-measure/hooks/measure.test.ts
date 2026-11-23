import { test, expect, mock } from 'claude-code/testing'
import { MEASURE } from './measure-cases'

const FILE = `/c/context/mod/${MEASURE.session_id}.json`

// The engine beneath the mod: env, the session's ids, the installed chela.json (`recorded`,
// or none), and every file written (or the write failing).
function world(on: any, env: Record<string, string>,
               o: { sid?: unknown, recorded?: string, writeFails?: boolean } = {}) {
  const w = { writes: [] as { path: string, text: string }[], reads: [] as string[], beneath: [] as any[] }
  mock.env(on, env)
  on('session.id', () => ({ value: 'sid' in o ? o.sid : MEASURE.session_id }))
  on('session.cwd', () => ({ value: MEASURE.cwd }))
  on('session.model', () => ({ value: MEASURE.model }))
  on('fs.read', (_: any, e: any) => {
    w.reads.push(e.path)
    if (o.recorded === undefined) throw new Error('ENOENT')
    return { value: o.recorded }
  })
  on('fs.write', (_: any, e: any) => {
    if (o.writeFails) throw new Error('EACCES')
    w.writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  // The engine's own answer, which the mod must hand back untouched.
  on('session.measure', (_: any, e: any) => { w.beneath.push(e); return { changed: e.changed } })
  return w
}
const measure = ($: any, e: any = MEASURE.event) => $.session.measure(e)

test('a measurement is ONE write: the statusLine-shaped file, tagged source "mod", under $CHELA_DIR', async ($, on) => {
  const w = world(on, { CHELA_DIR: '/c', HOME: '/h' })
  const out = await measure($)
  expect(w.writes.map(x => x.path)).toEqual([FILE])
  expect(JSON.parse(w.writes[0]!.text)).toEqual(MEASURE.file)
  expect(out).toEqual({ changed: ['context', 'rateLimits', 'cost'] })
  expect(w.beneath).toEqual([MEASURE.event])
})

test('the fixture file is the documented literal, not the code under test', () => {
  expect(MEASURE.file.source).toBe('mod')
  expect(MEASURE.file.rate_limits.five_hour).toEqual({ used_percentage: 23.5, resets_at: 1792238400 })
  expect(MEASURE.file.context_window).toEqual({ context_window_size: 200000, total_input_tokens: 84000,
                                                used_percentage: 42, remaining_percentage: 58 })
})

test('a figure the engine does not have is left out, never zeroed', async ($, on) => {
  const w = world(on, { CHELA_DIR: '/c' })
  await measure($, { context: { window: 200000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 5 }],
                     changed: ['rateLimits'] })
  const f = JSON.parse(w.writes[0]!.text)
  expect(f.context_window).toEqual({ context_window_size: 200000 })
  expect(f.rate_limits).toEqual({ five_hour: { used_percentage: 5 } })
  expect('cost' in f).toBe(false)
})

test('off a subscription (no rate limits) the file carries none, so the bars stay unknown', async ($, on) => {
  const w = world(on, { CHELA_DIR: '/c' })
  await measure($, { ...MEASURE.event, rateLimits: [], changed: ['context'] })
  expect(JSON.parse(w.writes[0]!.text).rate_limits).toEqual({})
})

// Where the file goes: $CHELA_DIR > the chela.json install-mod wrote > $HOME/.chela > nowhere.
for (const [name, env, recorded, path] of [
  ['env wins over chela.json', { CHELA_DIR: '/env', HOME: '/h' }, '{"chela_dir": "/rec"}', '/env'],
  ['chela.json wins over HOME', { HOME: '/h' }, '{"chela_dir": "/rec"}', '/rec'],
  ['HOME when nothing else', { HOME: '/h' }, undefined, '/h/.chela'],
  ['a blank CHELA_DIR is unset', { CHELA_DIR: '  ', HOME: '/h' }, undefined, '/h/.chela'],
  ['a chela.json that is not JSON is skipped', { HOME: '/h' }, 'nope', '/h/.chela'],
  ['a chela.json without a string chela_dir is skipped', { HOME: '/h' }, '{"chela_dir": 5}', '/h/.chela'],
  ['a blank chela_dir is skipped', { HOME: '/h' }, '{"chela_dir": " "}', '/h/.chela'],
] as const) {
  test(`the chela dir: ${name}`, async ($, on) => {
    const w = world(on, env as any, { recorded: recorded as any })
    await measure($)
    expect(w.writes.map(x => x.path)).toEqual([`${path}/context/mod/${MEASURE.session_id}.json`])
  })
}

test('the recorded chela dir is read from the installed copy\'s chela.json', async ($, on) => {
  const w = world(on, { HOME: '/h' }, { recorded: '{"chela_dir": "/rec"}' })
  await measure($)
  expect(w.reads.length).toBe(1)
  expect(w.reads[0]!.endsWith('/chela.json')).toBe(true)
})

test('no CHELA_DIR, no chela.json and no HOME: nothing is written, the event still passes', async ($, on) => {
  const w = world(on, {})
  const out = await measure($)
  expect([w.writes, out]).toEqual([[], { changed: ['context', 'rateLimits', 'cost'] }])
})

for (const sid of ['../../etc/passwd', 'abc', '', 'g0f0f0f0-0000', 42]) {
  test(`a session id that is not a safe file name (${JSON.stringify(sid)}) writes nothing`, async ($, on) => {
    const w = world(on, { CHELA_DIR: '/c' }, { sid })
    const out = await measure($)
    expect([w.writes, out]).toEqual([[], { changed: ['context', 'rateLimits', 'cost'] }])
  })
}

test('a failed write is a lost sample: the event still resolves with the engine\'s answer', async ($, on) => {
  const w = world(on, { CHELA_DIR: '/c' }, { writeFails: true })
  const out = await measure($)
  expect([w.beneath.length, out]).toEqual([1, { changed: ['context', 'rateLimits', 'cost'] }])
})

// CMX-477 update 2: chela's mod coexists with a person's own (an account switcher). Both get
// every measurement, in either order, and neither sees the other's files or state.
// An inline plugin runs in an environment of its own, so the switcher reports what it saw
// the only way a mod can: through `$`, by writing its own file.
const SWITCHER = {
  name: 'account-switcher',
  register: (on: any) => {
    on('session.measure', async ($: any, e: any, next: any) => {
      await $.fs.write('/switcher/seen.json', JSON.stringify(e))
      return next(e)
    })
  },
}
for (const tier of ['prepend', 'append', 'user'] as const) {
  test(`beside another user mod (${tier}), BOTH receive session.measure`, { plugins: [{ ...SWITCHER, tier }] },
    async ($, on) => {
      const w = world(on, { CHELA_DIR: '/c' })
      const out = await measure($)
      expect(w.writes.map(x => x.path).sort()).toEqual(['/switcher/seen.json', FILE].sort())
      expect(JSON.parse(w.writes.find(x => x.path === '/switcher/seen.json')!.text)).toEqual(MEASURE.event)
      expect(JSON.parse(w.writes.find(x => x.path === FILE)!.text)).toEqual(MEASURE.file)
      expect(w.beneath).toEqual([MEASURE.event])
      expect(out).toEqual({ changed: ['context', 'rateLimits', 'cost'] })
    })
}

// What chela cannot fix from its side: a mod ABOVE chela's that answers without `next(e)`
// keeps the event from every hook beneath it. Pinned so the spike's claim stays measured.
// In the test kit chela's mod sits above an inline plugin of its own tier ('user'); a real
// session's order within one tier is its load order, which this does not measure.
const SWALLOWER = {
  name: 'swallower',
  register: (on: any) => {
    on('session.measure', async (_: any, e: any) => ({ changed: e.changed }))
  },
}
for (const [tier, reaches] of [['prepend', false], ['append', true], ['user', true]] as const) {
  test(`a mod in ${tier} that never calls next: chela's ${reaches ? 'still writes' : 'gets nothing'}`,
    { plugins: [{ ...SWALLOWER, tier }] }, async ($, on) => {
      const w = world(on, { CHELA_DIR: '/c' })
      await measure($)
      expect(w.writes.map(x => x.path)).toEqual(reaches ? [FILE] : [])
    })
}
