// Guest-side handling of a HOST RESTART (CMX-434) — NO DOM, NO crypto, injectable
// timers, so it is unit-tested under node (tests/relay_restart.test.mjs).
//
// When the process hosting a share exits for a deploy, the host sends
// {"t":"restarting"} — not {"t":"ended"} — and keeps the share on disk; the next
// host restores it with the same room and pairing code. The guest page then:
//   * shows "host restarting…" instead of "This share has ended";
//   * re-sends its hello with backoff (500 ms doubling to 8 s), so the restored host
//     answers with a fresh keyframe and its resume challenge as soon as it is up;
//   * returns to live on the first frame the host sends;
//   * gives up after RESTART_GIVE_UP_MS and only THEN calls the share ended.
// The relay socket itself reconnects with backoff in index.html's ws.onclose.

export const RESTART_GIVE_UP_MS = 5 * 60 * 1000;
export const HELLO_BACKOFF_MIN = 500;
export const HELLO_BACKOFF_MAX = 8000;
export const RESTARTING_STATUS = 'host restarting…';
export const GAVE_UP_MESSAGE = 'This share has ended — the host did not come back.';

export class HostRestart {
  constructor({ hello, status, giveUp, setTimer = setTimeout, clearTimer = clearTimeout,
                giveUpMs = RESTART_GIVE_UP_MS }) {
    this._hello = hello; this._status = status; this._giveUp = giveUp;
    this._set = setTimer; this._clear = clearTimer; this._giveUpMs = giveUpMs;
    this.active = false; this._helloTimer = null; this._giveUpTimer = null;
    this.backoff = HELLO_BACKOFF_MIN;
  }

  // The host said it is restarting.
  start() {
    if (this.active) return;
    this.active = true;
    this.backoff = HELLO_BACKOFF_MIN;
    this._status(RESTARTING_STATUS);
    this._schedule();
    this._giveUpTimer = this._set(() => { this.stop(); this._giveUp(GAVE_UP_MESSAGE); }, this._giveUpMs);
  }

  _schedule() {
    this._helloTimer = this._set(() => {
      if (!this.active) return;
      this._hello();
      this.backoff = Math.min(this.backoff * 2, HELLO_BACKOFF_MAX);
      this._schedule();
    }, this.backoff);
  }

  // Any decrypted frame from the host. True when it ended a restart (back to live).
  frame() {
    if (!this.active) return false;
    this.stop();
    return true;
  }

  stop() {
    this.active = false;
    if (this._helloTimer != null) this._clear(this._helloTimer);
    if (this._giveUpTimer != null) this._clear(this._giveUpTimer);
    this._helloTimer = this._giveUpTimer = null;
  }
}

// The hello a guest sends: dims, plus the restored host's resume nonce once it has
// issued one — a restored host accepts input only from a stream that echoed it.
export const helloMessage = (cols, rows, resumeNonce) =>
  resumeNonce ? { t: 'hello', cols, rows, resume: resumeNonce } : { t: 'hello', cols, rows };

// A resume challenge's nonce, or null if the frame isn't a well-formed one.
export const resumeNonceOf = (m) =>
  m && m.t === 'resume' && typeof m.n === 'string' && /^[0-9a-f]{8,64}$/.test(m.n) ? m.n : null;
