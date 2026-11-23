// CMX-477: chela-measure, chela's usage feed. See measure.ts and docs/spikes/SESSION_MEASURE.md.
import type { Register } from 'claude-code'
import { record } from './measure'

export const register: Register = (on) => {
  // `next(e)` FIRST, unconditionally: the mods beneath this one (a person's own) get the
  // event whatever this one's write does. Then the write, which never throws.
  on('session.measure', async ($, e, next) => {
    const out = await next(e)
    await record($, e)
    return out
  })
}
