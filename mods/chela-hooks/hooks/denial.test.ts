import { test, expect, mock } from 'claude-code/testing'
import { DENIAL } from './denial-cases'
import { oneLine } from './denial'

const INBOX = 'http://127.0.0.1:5001/api/inbox/denial'

// The world beneath the mod: env, the network (recorded, or down), and the engine's own
// answer to the denial (`retry`), which the Mod must hand back untouched.
function world(on: any, env: Record<string, string>, fetchDown = false) {
  const w = { posts: [] as any[] }
  mock.env(on, env)
  on('session.id', () => ({ value: 's-1' }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('http.fetch', ($: any, e: any) => {
    if (fetchDown) throw new Error('ECONNREFUSED')
    w.posts.push(e)
    return { value: { status: 200, ok: true, headers: {}, text: '{"ok": true, "queued": 1}' } }
  })
  on('classic.PermissionDenied', () => ({ retry: true }))
  return w
}
const deny = ($: any) => $.classic.PermissionDenied(DENIAL.event as any)

test('a denial is ONE post to the inbox, with the tool, a one-line reason, the session and the window', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, { CHELA_WID: DENIAL.wid })
  const out = await deny($)
  await clock.settle()
  expect(w.posts.length).toBe(1)
  expect(w.posts[0].url).toBe(INBOX)
  expect(w.posts[0].init.method).toBe('POST')
  expect(w.posts[0].init.headers).toEqual({ 'Content-Type': 'application/json' })
  expect(JSON.parse(w.posts[0].init.body)).toEqual(DENIAL.body)
  expect(out).toEqual({ retry: true })
})

test('the fixture body is the documented literal, not the code under test', () => {
  expect(DENIAL.body).toEqual({ wid: '@41', session_id: 's-1', tool_name: 'Bash',
    tool_input: { command: 'rm -rf build' }, tool_use_id: 'toolu_01denied',
    reason: 'Deletes files outside the task' })
})

test('with the event post on too, the inbox still gets exactly one post', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, { CHELA_WID: DENIAL.wid, CHELA_MOD_HOOKS_URL: 'http://127.0.0.1:5999' })
  await deny($)
  await clock.settle()
  expect(w.posts.map(p => p.url).sort()).toEqual([INBOX, 'http://127.0.0.1:5999/hooks/PermissionDenied'])
})

for (const [raw, port] of [['5123', '5123'], ['', '5001'], ['nope', '5001']] as const) {
  test(`CHELA_DASHBOARD_PORT=${JSON.stringify(raw)} posts to port ${port}`, async ($, on) => {
    const clock = mock.clock(on)
    const w = world(on, { CHELA_WID: DENIAL.wid, CHELA_DASHBOARD_PORT: raw })
    await deny($)
    await clock.settle()
    expect(w.posts.map(p => p.url)).toEqual([`http://127.0.0.1:${port}/api/inbox/denial`])
  })
}

test('nothing is posted outside a chela window ($CHELA_WID unset)', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, {})
  expect(await deny($)).toEqual({ retry: true })
  await clock.settle()
  expect(w.posts).toEqual([])
})

test('the daemon is down: the denial event and the next tool call are unaffected (fails open)', async ($, on) => {
  const clock = mock.clock(on)
  world(on, { CHELA_WID: DENIAL.wid, CHELA_MOD_MERGEGATE: '/gate.py' }, true)
  const ran: string[] = []
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '' } }))
  on('tool.call', ($: any, e: any) => { ran.push(e.command); return { result: 'ok' } })
  expect(await deny($)).toEqual({ retry: true })
  await clock.settle()
  expect(await $.tool.call({ tool: 'Bash', command: 'ls', tool_use_id: 'tu-2' } as any)).toEqual({ result: 'ok' })
  expect(ran).toEqual(['ls'])
})

test('the reason is one line, clipped to 200 characters with an ellipsis', () => {
  expect(oneLine(' a\n\tb  c ', 200)).toBe('a b c')
  expect(oneLine(undefined, 200)).toBe('')
  expect(oneLine('x'.repeat(200), 200)).toBe('x'.repeat(200))
  expect(oneLine('x'.repeat(201), 200)).toBe(`${'x'.repeat(199)}…`)
  // code points, as the daemon counts: 200 emoji are 400 UTF-16 units and still fit
  expect(oneLine('😀'.repeat(200), 200)).toBe('😀'.repeat(200))
  expect(oneLine('😀'.repeat(201), 200)).toBe(`${'😀'.repeat(199)}…`)
})
