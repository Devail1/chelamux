// Mod 4/4 (CMX-184): a classifier denial goes straight to the orchestrator's inbox. On
// classic.PermissionDenied the Mod POSTs the tool, a one-line reason, the session and the
// window to the daemon's /api/inbox/denial, which files it as CMX-155's notice: no event-log
// tail. plugin/'s shell hook still logs the same denial; the daemon keys both on the
// tool_use_id, so one denial is one notice with both layers loaded.
//
// Not awaited and never throws: a dead daemon is a lost post, never a stalled tool call.
// Gated on $CHELA_WID like every other chela post. Under --permission-mode dontAsk Claude
// Code fires no PermissionDenied at all, in either layer.
// register.ts hooks it, since the validator wants each `on()` there, literal.
import { dashboardPort } from './needsyou'

export const REASON_CHARS = 200

// whitespace collapsed to single spaces, clipped to `limit` with an ellipsis
export function oneLine(text: unknown, limit: number): string {
  const s = String(text ?? '').split(/\s+/).filter(Boolean).join(' ')
  return s.length > limit ? `${s.slice(0, limit - 1)}…` : s
}

export function denialBody(e: any, wid: string) {
  return {
    wid, session_id: e.session_id ?? '', tool_name: e.tool_name ?? '',
    tool_input: e.tool_input ?? {}, tool_use_id: e.tool_use_id ?? '',
    reason: oneLine(e.reason, REASON_CHARS),
  }
}

export async function report($: any, e: any) {
  try {
    const wid = await $.env.get('CHELA_WID')
    if (!wid) return
    const port = dashboardPort(await $.env.get('CHELA_DASHBOARD_PORT'))
    await $.http.fetch(`http://127.0.0.1:${port}/api/inbox/denial`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(denialBody(e, wid)),
    })
  } catch {
    // the shell hook's event-log copy still reaches the inbox
  }
}
