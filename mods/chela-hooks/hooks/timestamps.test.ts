import { test, expect, mock } from 'claude-code/testing'
import { TIMESTAMPS } from './timestamps-cases'
import { flagOn, hhmm } from './timestamps'

// 2026-10-19T12:34:00Z
const NOW = 1792413240000
const BAND = { component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false } } as const

// The world beneath the mod. The bottom MessageDisplay hook stands in for plugin/'s: it
// answers as the daemon's message_display_response does (tests/test_mods_timestamps.py
// proves that on the real one), stamping the reply unless the handshake field came down.
function world(on: any, env: Record<string, string>, base = '2.1.302') {
  const w = { seen: [] as any[] }
  on('env.get', (_: any, e: any) => ({ value: env[e.name] }))   // read live: a test may change it
  // the engine's own band, which the stamp must sit under, never replace
  on('ui.render', ($: any, e: any) => {
    const { Box, Text } = $.ui.resolve(e)
    return h(Box, { key: 'engine' }, h(Text, {}, 'ENGINE'))
  })
  on('session.version', () => ({ value: { version: base, base } }))
  on('classic.MessageDisplay', (_: any, e: any) => {
    w.seen.push(e)
    return e.index !== 0 || e.chela_timestamp_owner === 'mod' ? {} : { displayContent: `**[12:34]** ${e.delta}` }
  })
  return w
}
const display = ($: any, index = 0) => $.classic.MessageDisplay({ ...TIMESTAMPS.body, index })

async function bandText($: any) {
  const ui = await $.ui.mount({ plugin: 'chela-hooks-mod', surface: 'terminal', ...BAND })
  const found = await ui.findAll({ type: 'Text' })
  await ui.unmount()
  return found.map((t: any) => t.text).join('')
}

test('flag on: the band shows the last reply\'s time and plugin/ does not stamp it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const w = world(on, { CHELA_TERMINAL_TIMESTAMPS: 'true' })
  expect(await bandText($)).toBe('ENGINE')
  expect(await display($)).toEqual({})
  expect(w.seen[0].chela_timestamp_owner).toBe('mod')
  const first = hhmm(NOW, new Date(NOW).getTimezoneOffset())
  expect(await bandText($)).toBe(`ENGINE[${first}] last reply`)
  // a later batch of the same reply keeps the time; the next reply moves it
  await clock.advance(5 * 60000)
  expect(await display($, 3)).toEqual({})
  expect(await bandText($)).toBe(`ENGINE[${first}] last reply`)
  expect(await display($, 0)).toEqual({})
  expect(await bandText($)).toBe(`ENGINE[${hhmm(NOW + 5 * 60000, new Date(NOW).getTimezoneOffset())}] last reply`)
  expect(w.seen.every(e => e.chela_timestamp_owner === 'mod')).toBe(true)
})

for (const [name, env, base] of [
  ['the flag is unset', {}, '2.1.302'],
  ['the flag is off', { CHELA_TERMINAL_TIMESTAMPS: 'off' }, '2.1.302'],
  ['Claude Code is below 2.1.301', { CHELA_TERMINAL_TIMESTAMPS: 'true' }, '2.1.300'],
] as const) {
  test(`${name}: no band, and plugin/ stamps the reply as before`, async ($, on) => {
    mock.clock(on, { now: NOW })
    const w = world(on, env, base)
    expect(await display($)).toEqual({ displayContent: `**[12:34]** ${TIMESTAMPS.body.delta}` })
    expect('chela_timestamp_owner' in w.seen[0]).toBe(false)
    expect(await bandText($)).toBe('ENGINE')
  })
}

test('the flag turned off after a reply: the band goes, even with a time recorded', async ($, on) => {
  mock.clock(on, { now: NOW })
  const env: Record<string, string> = { CHELA_TERMINAL_TIMESTAMPS: 'true' }
  world(on, env)
  await display($)
  expect(await bandText($)).toMatch(/^ENGINE\[\d\d:\d\d\] last reply$/)
  env.CHELA_TERMINAL_TIMESTAMPS = 'false'
  expect(await bandText($)).toBe('ENGINE')
})

test('the band yields to a survey', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, { CHELA_TERMINAL_TIMESTAMPS: '1' })
  await display($)
  const ui = await $.ui.mount({ plugin: 'chela-hooks-mod', surface: 'terminal',
    component: 'AbovePrompt', props: { hasSurvey: true, isWorking: false } })
  expect(await ui.findAll({ type: 'Text', text: /last reply/ })).toEqual([])
  await ui.unmount()
})

test('the flag reads as chela.config reads CHELA_TERMINAL_TIMESTAMPS', () => {
  for (const raw of ['true', '1', 'yes', 'on', ' TRUE ', 'anything'])
    expect([raw, flagOn(raw)]).toEqual([raw, true])
  for (const raw of [undefined, 'false', '0', 'no', 'off', ' Off\n'])
    expect([raw, flagOn(raw)]).toEqual([raw, false])
})

test('HH:MM in local time: IDT is UTC+3, IST UTC+2, padded', () => {
  expect(hhmm(NOW, -180)).toBe('15:34')
  expect(hhmm(NOW, -120)).toBe('14:34')
  expect(hhmm(Date.UTC(2026, 0, 1, 22, 5), -120)).toBe('00:05')
  expect(hhmm(Date.UTC(2026, 0, 1, 7, 9), 0)).toBe('07:09')
})
