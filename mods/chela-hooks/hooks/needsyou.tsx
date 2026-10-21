// Mod 3/4 (CMX-183): what is waiting on the human, as one band above the prompt of the
// orchestrator session only (the window `chela watching` names): pending override approvals,
// pending access requests and runs in needs_human. Counts plus the oldest item, each a link
// to where the dashboard shows it. Read-only: approving stays in Telegram, the dashboard or
// a plain terminal.
//
// A timer polls the daemon's GET /api/needs-you every 10 s; no tool call waits on it. A
// failed or malformed read clears the band, and a reading older than 30 s is never drawn,
// so a dead or hung daemon shows nothing rather than a stale count.
// register.ts hooks it, since the validator wants each `on()` there, literal.
import { atom, read, update } from 'claude-code'
import type { NeedsYou, NeedsYouItem } from '../types'
import { atFloor, orchestratorWid } from './status'

export const POLL_MS = 10000
export const STALE_MS = 30000
const KINDS = ['approval', 'access', 'run'] as const
const NOUN: Record<NeedsYouItem['kind'], [string, string]> = {
  approval: ['approval', 'approvals'],
  access: ['access request', 'access requests'],
  run: ['run in needs_human', 'runs in needs_human'],
}

export const needsYou = atom({ plugin: 'chela-hooks-mod', key: 'needsYou' } as const, null as NeedsYou)

// chela.config.dashboard_port: CHELA_DASHBOARD_PORT, else 5001 (and 5001 when it is not a number).
export function dashboardPort(raw: string | undefined): string {
  const v = (raw ?? '').trim()
  return /^\d+$/.test(v) ? v : '5001'
}

// The daemon's answer, or null when it is not the documented shape: unknown is never "none".
export function parseItems(text: string): NeedsYouItem[] | null {
  try {
    const items = JSON.parse(text)?.items
    // not an array (absent, an object, a string): `.every` throws, and the catch answers null
    const ok = items.every((i: any) => i && (KINDS as readonly string[]).includes(i.kind)
      && typeof i.label === 'string' && typeof i.url === 'string' && Number.isFinite(i.since))
    return ok ? items : null
  } catch {
    return null
  }
}

// The item waiting longest; ties go to the first listed.
export function oldestOf(items: NeedsYouItem[]): NeedsYouItem | undefined {
  return items.reduce<NeedsYouItem | undefined>((a, b) => (!a || b.since < a.since ? b : a), undefined)
}

export function age(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

async function isOrchestrator($: any): Promise<boolean> {
  const wid = (await $.env.get('CHELA_WID')) || ''
  return !!wid && wid === await orchestratorWid($)
}

// One poll: the reading it gets, or null for anything that is not a fresh, readable answer.
export async function poll($: any, current: () => boolean = () => true) {
  let next: NeedsYou = null
  try {
    if (atFloor((await $.session.version()).base) && await isOrchestrator($)) {
      const port = dashboardPort(await $.env.get('CHELA_DASHBOARD_PORT'))
      const res = await $.http.fetch(`http://127.0.0.1:${port}/api/needs-you`)
      const items = res.ok ? parseItems(res.text) : null
      if (items) next = { at: await $.clock.now(), items }
    }
  } catch {
    next = null                               // a dead daemon shows nothing
  }
  if (current()) await update($, needsYou, () => next)
}

let timer: { cancel: () => void } | undefined
let generation = 0

// Started at session start; a reload runs `register` and `session.start` again, so the old
// timer is cancelled first, and a poll the old timer left waiting never writes (its
// generation is gone). A poll still waiting on the daemon skips the tick, and the tick
// clears a reading that has gone stale meanwhile, so the band goes even while hung.
export async function start($: any) {
  timer?.cancel()
  const mine = ++generation
  let inFlight = false
  const tick = async () => {
    const cur = await read($, needsYou)
    if (cur && (await $.clock.now()) - cur.at > STALE_MS) await update($, needsYou, () => null)
    if (inFlight) return
    inFlight = true
    try { await poll($, () => generation === mine) } finally { inFlight = false }
  }
  timer = $.clock.every(POLL_MS, () => { void tick() })
  void tick()
}

export async function band($: any, e: any, next: any) {
  const data = await read($, needsYou)
  if (e.props.hasSurvey || !data || !data.items.length) return next(e)
  const now: number = await $.clock.now()
  if (now - data.at > STALE_MS) return next(e)
  const below = await next(e)
  const { Box, Text, Link } = $.ui.resolve(e)
  const parts: any[] = []
  for (const kind of KINDS) {
    const of = data.items.filter((i: NeedsYouItem) => i.kind === kind)
    const first = oldestOf(of)
    if (!first) continue
    if (parts.length) parts.push(<Text key={`sep-${kind}`} dimColor> · </Text>)
    parts.push(<Link key={`count-${kind}`} href={first.url}
      label={`${of.length} ${NOUN[kind][of.length === 1 ? 0 : 1]}`} />)
  }
  const oldest = oldestOf(data.items) as NeedsYouItem
  return (
    <Box flexDirection="column">
      {below}
      <Box key="needs-you">
        <Text bold>needs you: </Text>
        {parts}
        <Text dimColor> · oldest: </Text>
        <Link key="oldest" href={oldest.url} label={`${NOUN[oldest.kind][0]} ${oldest.label}`} />
        <Text dimColor> ({age(now / 1000 - oldest.since)})</Text>
      </Box>
    </Box>
  )
}
