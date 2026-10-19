// Mod 2/4 (CMX-182): the terminal-wall timestamp as a band above the prompt, in place of
// the `**[HH:MM]**` the plugin/ path writes into each reply through MessageDisplay's
// `displayContent` (CMX-277/283/285). Off unless CHELA_TERMINAL_TIMESTAMPS is on, as
// chela.config reads it. While the band is on, every MessageDisplay the plugin/ hook pipes
// to the daemon carries the handshake field (timestamps-cases.ts), and the daemon steps
// aside, so one reply never shows its time in both places.
// register.ts hooks it, since the validator wants each `on()` there, literal.
import { atom, read, update } from 'claude-code'
import type { Stamp } from '../types'
import { atFloor } from './status'
import { TIMESTAMPS } from './timestamps-cases'

export const lastStamp = atom({ plugin: 'chela-hooks-mod', key: 'lastStamp' } as const, null as Stamp)

// chela.config.TERMINAL_TIMESTAMPS: unset is off; set, anything but false/0/no/off is on.
export function flagOn(raw: string | undefined): boolean {
  return raw !== undefined && !['false', '0', 'no', 'off'].includes(raw.trim().toLowerCase())
}

// Local wall-clock HH:MM, `offsetMin` as Date#getTimezoneOffset gives it (UTC minus local).
export function hhmm(ms: number, offsetMin: number): string {
  const d = new Date(ms - offsetMin * 60000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

export async function active($: any): Promise<boolean> {
  return flagOn(await $.env.get('CHELA_TERMINAL_TIMESTAMPS')) && atFloor((await $.session.version()).base)
}

// A reply's first batch is the moment the plugin/ path stamps; the band keeps the last one.
export async function onDisplay($: any, e: any, next: any) {
  if (!(await active($))) return next(e)
  if (e.index === 0) {
    const now: number = await $.clock.now()
    await update($, lastStamp, () => hhmm(now, new Date(now).getTimezoneOffset()))
  }
  return next({ ...e, ...TIMESTAMPS.handshake })
}

export async function band($: any, e: any, next: any) {
  const stamp = await read($, lastStamp)
  if (e.props.hasSurvey || stamp === null || !(await active($))) return next(e)
  // the stamp goes under whatever the engine and the plugins beneath draw, never in place of it
  const below = await next(e)
  const { Box, Text } = $.ui.resolve(e)
  return (
    <Box flexDirection="column">
      {below}
      <Box>
        <Text bold>[{stamp}]</Text>
        <Text dimColor> last reply</Text>
      </Box>
    </Box>
  )
}
