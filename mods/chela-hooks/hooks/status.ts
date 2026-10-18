// Mod 1/4 (CMX-181): chela's status line through `$.ui.status` (context bar, window id,
// orchestrator / run marker), plus the per-turn cache write `chela install-statusline`
// wires through settings.json: `$.session.usage()` (the status line's own figures) reshaped
// into the statusLine payload and piped to the SAME scripts/cache-statusline.sh.
// register.ts hooks it, since the validator wants each `on()` there, literal.
import type { SessionUsage } from 'claude-code'

// The first Claude Code with Mods. An older or unreadable version draws nothing.
export function atFloor(base: string | undefined): boolean {
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(base ?? '')
  return !!m && Number(m[1]) * 1e8 + Number(m[2]) * 1e4 + Number(m[3]) >= 2 * 1e8 + 1 * 1e4 + 301
}

export function bar(percent: number): string {
  const full = Math.max(0, Math.min(10, Math.round(percent / 10)))
  return '▰'.repeat(full) + '▱'.repeat(10 - full)
}

// A field the session does not have is left out, never drawn as "undefined".
export function statusText(f: { percent?: number, wid?: string, orchestrator?: boolean,
                                 run?: string, role?: string }): string | undefined {
  const parts: string[] = []
  if (typeof f.percent === 'number') parts.push(`ctx ${bar(f.percent)} ${Math.round(f.percent)}%`)
  if (f.wid) parts.push(f.wid)
  if (f.orchestrator) parts.push('orchestrator')
  if (f.run) parts.push(f.role ? `run ${f.run} (${f.role})` : `run ${f.run}`)
  return parts.length ? parts.join(' · ') : undefined
}

// The statusLine payload chela.context reads (`_parse_cache_file`), built from `u`.
export function cachePayload(session_id: string, cwd: string, model: string, u: SessionUsage) {
  const ctx: Record<string, number> = { context_window_size: u.context.window }
  if (typeof u.context.tokens === 'number') ctx.total_input_tokens = u.context.tokens
  if (typeof u.context.percent === 'number') {
    ctx.used_percentage = u.context.percent
    ctx.remaining_percentage = 100 - u.context.percent
  }
  const rate_limits: Record<string, Record<string, number>> = {}
  for (const r of u.rateLimits) {
    const at = Date.parse(r.resetsAt ?? '')
    rate_limits[r.kind] = Number.isNaN(at) ? { used_percentage: r.percentUsed }
      : { used_percentage: r.percentUsed, resets_at: Math.floor(at / 1000) }
  }
  return { session_id, cwd, workspace: { current_dir: cwd }, model: { id: model, display_name: model },
    context_window: ctx, rate_limits, ...(u.cost ? { cost: { total_cost_usd: u.cost.usd } } : {}) }
}

// The env pin, else the window `chela watch` recorded in inbox.json, as
// chela.inbox.orchestrator_wid reads it. Unreadable reads as no orchestrator.
async function orchestratorWid($: any): Promise<string | undefined> {
  const pin = ((await $.env.get('CHELA_ORCHESTRATOR_WID')) || '').trim()
  if (pin) return pin
  const dir = (await $.env.get('CHELA_DIR')) || `${await $.env.get('HOME')}/.chela`
  try {
    // a non-string never equals the string window id it is compared with
    return JSON.parse(await $.fs.read((await $.env.get('CHELA_INBOX_FILE')) || `${dir}/inbox.json`))?.orchestrator
  } catch {
    return undefined
  }
}

export async function refresh($: any, options: Record<string, unknown>) {
  if (options.statusLine === false || !atFloor((await $.session.version()).base)) {
    $.ui.status(undefined)
    return
  }
  const usage: SessionUsage = await $.session.usage()
  const wid = (await $.env.get('CHELA_WID')) || undefined
  const orch = wid ? await orchestratorWid($) : undefined
  $.ui.status(statusText({ percent: usage.context.percent, wid, orchestrator: !!wid && wid === orch,
    run: (await $.env.get('CHELA_RUN_ID')) || undefined, role: (await $.env.get('CHELA_RUN_ROLE')) || undefined }))
  const script = (await $.env.get('CHELA_MOD_STATUSLINE')) || `${$.plugin.root}/../../scripts/cache-statusline.sh`
  const payload = cachePayload(await $.session.id(), await $.session.cwd(), await $.session.model(), usage)
  // The script logs its own failures to $CHELA_DIR/statusline-errors.log (CMX-30).
  await $.process.run(['bash', script], { stdin: JSON.stringify(payload), timeoutMs: 10000 })
    .catch(() => undefined)
}
