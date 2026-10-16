import { test, expect, mock } from 'claude-code/testing'
import { CASES } from './cases'

const URL = 'http://127.0.0.1:5999'
const ENV = { CHELA_WID: '@7', CHELA_MOD_HOOKS_URL: URL, CHELA_MOD_MERGEGATE: '/gate.py' }
const denyOut = (reason: string) => JSON.stringify({ hookSpecificOutput: {
  hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: reason } })

// The world beneath the mod: env, session, the network (recorded), the gate process
// (answered by `gateOut`), and the tool itself (recorded: it RAN).
function world(on: any, env: Record<string, string>, gateOut: (stdin: any) => any, fetchDown = false) {
  const w = { posts: [] as any[], runs: [] as any[], ran: [] as string[] }
  mock.env(on, env)
  on('session.id', () => ({ value: 's-1' }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('http.fetch', ($: any, e: any) => {
    if (fetchDown) throw new Error('ECONNREFUSED')
    w.posts.push(e)
    return { value: { status: 200, ok: true, headers: {}, text: '' } }
  })
  on('process.run', ($: any, e: any) => { w.runs.push(e); return { value: gateOut(JSON.parse(e.init.stdin)) } })
  on('tool.call', ($: any, e: any) => { w.ran.push(e.command ?? e.file_path); return { result: 'ok' } })
  for (const ev of ['SessionStart', 'SessionEnd', 'Stop', 'PostToolUse', 'PermissionDenied'])
    on(`classic.${ev}`, () => ({}))
  return w
}
const bash = ($: any, command: string) => $.tool.call({ tool: 'Bash', command, tool_use_id: 'tu-1' })

for (const c of CASES.gate) {
  test(`gate ${c.deny ? 'DENIES' : 'allows'}: ${c.command}`, async ($, on) => {
    const clock = mock.clock(on)
    const w = world(on, ENV, p => ({ exitCode: 0, stderr: '',
      stdout: c.deny && p.tool_input.command === c.command ? denyOut(`no: ${c.command}`) : '' }))
    const out: any = await bash($, c.command)
    await clock.settle()
    expect(w.runs).toEqual([{ argv: ['python3', '-I', '-S', '/gate.py'],
      init: { stdin: expect.any(String), timeoutMs: 5000 } }])
    expect(JSON.parse(w.runs[0].init.stdin)).toEqual({ session_id: 's-1', transcript_path: '',
      cwd: '/repo', hook_event_name: 'PreToolUse', tool_name: 'Bash',
      tool_input: { command: c.command }, tool_use_id: 'tu-1' })
    expect(w.ran).toEqual(c.deny ? [] : [c.command])
    expect(out).toEqual(c.deny ? { deny: `no: ${c.command}` } : { result: 'ok' })
    // a denied call is still posted, as the shell plugin's side-by-side hooks post it
    expect(w.posts.map(p => p.url)).toEqual([`${URL}/hooks/PreToolUse`])
  })
}

for (const [name, answer] of [
  ['the gate cannot start', () => { throw new Error('ENOENT') }],
  ['the gate exits nonzero', () => ({ exitCode: 1, stdout: denyOut('x'), stderr: '' })],
  ['the gate prints garbage', () => ({ exitCode: 0, stdout: '{not json', stderr: '' })],
  ['the gate prints an allow', () => ({ exitCode: 0, stdout: JSON.stringify({ hookSpecificOutput:
    { permissionDecision: 'allow', permissionDecisionReason: 'x' } }), stderr: '' })],
] as const) {
  test(`fails OPEN when ${name}`, async ($, on) => {
    const w = world(on, ENV, answer as any)
    await bash($, 'gh pr merge 5')
    expect(w.ran).toEqual(['gh pr merge 5'])
  })
}

test('the gate defaults to the repo plugin script', async ($, on) => {
  const w = world(on, { CHELA_WID: '@7' }, () => ({ exitCode: 0, stdout: '', stderr: '' }))
  await bash($, 'ls')
  expect(w.runs[0].argv[3]).toMatch(/^\/.*\/mods\/chela-hooks\/\.\.\/\.\.\/plugin\/hooks\/mergegate\.py$/)
})

test('a non-Bash tool is posted and never gated', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, ENV, () => { throw new Error('gated') })
  await $.classic.Stop({ stop_hook_active: false, last_assistant_message: 'x', transcript_path: '/t.jsonl' } as any)
  await $.tool.call({ tool: 'Read', file_path: '/a', tool_use_id: 'tu-9' } as any)
  await clock.settle()
  expect(w.runs).toEqual([])
  expect(w.ran).toEqual(['/a'])
  const pre = w.posts.find(p => p.url.endsWith('/hooks/PreToolUse'))
  expect(JSON.parse(pre.init.body)).toEqual(CASES.pre_tool_use)
})

for (const ev of CASES.events) {
  test(`posts ${ev.hook_event_name} verbatim to /hooks/${ev.hook_event_name}`, async ($, on) => {
    const clock = mock.clock(on)
    const w = world(on, ENV, () => ({ exitCode: 0, stdout: '', stderr: '' }))
    const { hook_event_name, ...fields } = ev as any
    await ($.classic as any)[hook_event_name](fields)
    await clock.settle()
    expect(w.posts.length).toBe(1)
    const p = w.posts[0]
    expect(p.url).toBe(`${URL}/hooks/${hook_event_name}`)
    expect(p.init.method).toBe('POST')
    expect(p.init.headers['Content-Type']).toBe('application/json')
    expect(p.init.headers['X-Chela-Wid']).toBe(hook_event_name === 'SessionStart' ? '@7' : undefined)
    expect(JSON.parse(p.init.body)).toEqual(ev)
  })
}

test('nothing is posted without $CHELA_WID, and the gate still holds', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, { CHELA_MOD_HOOKS_URL: URL }, () => ({ exitCode: 0, stdout: denyOut('no'), stderr: '' }))
  await $.classic.Stop({ stop_hook_active: false, last_assistant_message: 'x' } as any)
  await bash($, 'gh pr merge 5')
  await clock.settle()
  expect(w.posts).toEqual([])
  expect(w.ran).toEqual([])
})

test('the daemon URL defaults to 127.0.0.1:5001', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, { CHELA_WID: '@7' }, () => ({ exitCode: 0, stdout: '', stderr: '' }))
  await $.classic.Stop({ stop_hook_active: false, last_assistant_message: 'x' } as any)
  await clock.settle()
  expect(w.posts.map(p => p.url)).toEqual(['http://127.0.0.1:5001/hooks/Stop'])
})

test('a dead daemon never fails the tool call', async ($, on) => {
  const clock = mock.clock(on)
  const w = world(on, ENV, () => ({ exitCode: 0, stdout: '', stderr: '' }), true)
  await bash($, 'ls')
  await clock.settle()
  expect(w.ran).toEqual(['ls'])
})
