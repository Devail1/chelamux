### Changed

- **A message to an agent is never typed into its pane any more.** `chela msg`, `chela
  broadcast`, room handoffs/questions/blockers, the fleet pause, the decisions inbox and the
  usage-outage resume used to fall back to a tmux paste, without saying so, when the target's
  peer-messaging socket could not be reached. That paste arrived as a bare user turn, so
  another agent's text read as if the user had written it. Now an unreachable socket returns
  `unreachable`: the room delivery stays parked, the inbox event and the resume stay queued,
  a `peer_unreachable` event is logged, and `chela msg` / `chela room post` exit non-zero
  naming the window and the reason. `chela doctor` reports such windows as "unreachable:
  messages are held". (CMX-482)
- **`chela drive` sends a native peer message** from the driving window instead of typing into
  the sibling's pane, and exits non-zero when it is not delivered. (CMX-482)
- **The dashboard's "message agent" box sends your text as your own prompt** (`deliver_prompt`,
  refused with a 409 when the prompt already holds an unsent draft) rather than as a message
  from `dashboard`. (CMX-482)
