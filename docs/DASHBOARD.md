# The dashboard

`uv run chela dashboard` serves the web UI on `http://127.0.0.1:5001` (`--host` / `--port`,
or `CHELA_DASH_HOST` / `CHELA_DASHBOARD_PORT`). It needs the `dashboard` extra, which
`uv sync --all-extras` installs. It binds loopback and has no login of its own: reach it
remotely through a tailnet or an SSH tunnel ([the security notes](https://chela.pages.dev/docs#security)).

The page is a React app (`docs/FRONTEND.md`), served as a committed build, so running it needs
no Node. There are two views and a Settings panel:

- **Wall** — every agent's live terminal, each pane marked *working*, *needs you*, *PR open*
  or *idle*, with a context bar, its tmux id (`@N`, click to copy), and file drop / paste into
  the session's `uploads/` folder. The crisp ring marks the pane your keyboard is in; a pane
  waiting on you glows yellow.
- **Work** — the dispatch board, the runs and the schedules.
- **Settings** — <kbd>Ctrl</kbd>+<kbd>,</kbd> (<kbd>⌘</kbd>+<kbd>,</kbd> on macOS), with a
  search box over every setting. Tabs: General, Timing, Dispatch, Notifications, Cost,
  Appearance, Collaboration.

## The sidebar

Sessions are grouped by **project folder**, like the Claude desktop app. Each folder header
has a `+` (new session in that folder), a collapse chevron and a menu (right-click, or ⋯ on a
phone). A session started in your home directory groups under the home folder's name; only
windows with no working directory fall into **Other**.

- **Dispatched work is one row per run**, `CMX-N · <title>`, in a *Dispatched* group: the
  agent and judge windows fold into it, and while the judge's battery runs the row carries a judge chip,
  `3/6` (its tooltip: `Judge testing · 3/6 · 15m`).
- **The orchestrator's row carries an `orchestrator` badge.** The inbox icon in the sidebar
  header jumps to it (focusing its pane and expanding its group) and opens the decisions log.
  It is not pinned: it stays in its folder group.
- **The VIEW menu** (the sliders button on the Sessions header) picks **Status** (Active / All /
  Archived), **Environment** (Interactive, Dispatched agents, Judges, Background sessions),
  **Last activity** (1d / 7d / 30d / All), **Group by** (Date, Folder, State, Custom groups,
  None) and **Sort by** (Last activity, Name, Created), plus *Show empty groups* and *Show PR
  status*. A filter never hides a session that is working or waiting on you, or the
  orchestrator.
- **The layout follows you across devices.** Group order, custom groups and the VIEW choices
  are stored by chela (`/api/sidebar/layout`), so the phone and the laptop show the same
  sidebar. Collapsed groups, the collapsed sidebar and the Archived section's open state stay
  per device. A newly seen folder group lands among the folders.
- **Hold <kbd>Alt</kbd> to number the rows**; <kbd>Alt</kbd>+<kbd>1</kbd>..<kbd>9</kbd> opens
  that session, in any view and from inside a focused terminal. The numbers go on the first
  nine visible session rows; collapsed groups and Archived are skipped.
- **Custom groups** are created, renamed and deleted in the VIEW menu. A row's
  **Move to group…** item appears only when Group by is *Custom groups*.

### Archive

Archive works like the desktop app: **archiving a session closes it, and Unarchive resumes
it.** Archiving records the session id, folder, window name and Remote Control name, closes
the window and closes its Telegram topic. Unarchive opens a new window in the same folder
under the same name, runs `claude --resume`, and reopens the topic.

- Every row's menu has **Archive**, and a group's menu has **Archive all (N)**. Both take
  finished sessions and idle ones (waiting for your next message).
- They never take a busy session, one waiting on you, the orchestrator, a judge mid-battery,
  an unsettled run, or a session whose status or id cannot be read.
- A settled dispatched run is only hidden: its windows belong to the dispatcher.
- Archived rows sit in one collapsed **Archived (N)** section at the bottom. Unarchive opens
  one window per session, even if it is tapped twice.
- Archive state is kept by chela in `$CHELA_DIR/sidebar-archive.json`
  (`chela/sidebar_archive.py`), so every device shows the same set.

## Window names and Remote Control

A window chela opens (the dashboard launcher, Telegram `/new`, `chela spawn`, a resume) is
named after its folder: the cwd basename, or your login for the home directory, with a `-N`
suffix if taken. Claude Code starts with `--remote-control <that name>`, so claude.ai and the
Claude desktop show the same name. Turn that off in Settings → General → Remote Control, or
with `CHELA_REMOTE_CONTROL=false`. Dispatched agents and judges never get Remote Control.
A `claude` you type yourself in such a window (a "+ New shell", or after the first
session exits) gets the same flag: chela defines a `claude` shell function there while the
setting is on. `-p`/`--print`, `--help`/`--version`, a prompt or subcommand, or your own
`--remote-control` pass through unchanged. Turning the setting off affects new windows
only: an already-open shell keeps the function until it exits.

While chela still owns a window's name, the name follows the session's folder: open a window
in `~`, `cd` its Claude into `~/projects/app`, and after a full tick in the new folder the
window becomes `app` (with `-N` if taken), along with its Telegram topic and Remote Control
name. A name you chose, the orchestrator's window, and dispatched agent and judge windows are
never renamed this way. Otherwise chela renames a window only to resolve a duplicate (the
newer one gets the `-N`). A rename
made **in the dashboard** marks the name manual, so it is never suffixed, and chela types
`/rename <new name>` into the session once its prompt is empty. A raw `tmux rename-window` is
not treated as manual, and a rename made in the desktop app does not sync back.

The row and pane header show the manual name if there is one, else Claude's session title,
else the window name.

## The Work board

- With a Linear tracker the columns are the team's workflow states, in Linear's order, and
  each card links to its issue (`CMX-12 ↗`). **New task** creates a Linear issue in the
  workflow's ready state; only the dashboard calls Linear, so `LINEAR_API_KEY` never reaches
  the page. Other trackers keep chela's own lanes.
- A closed run that stalls a ready task is flagged on its card with a **Requeue** button
  (**Close & requeue** while it is still in flight). See [RUNS.md](RUNS.md#closing-and-requeueing).

- A card whose agent handed in with `chela task-finished --handoff "…"` shows that note for
  its reviewer.

## Pending approvals

An approvals pill on every view counts the override and release requests waiting on you. Its sheet
shows what is asked, who asked, why, its age and time to expiry. **Approve** (a second tap
confirms) and **Deny** go through the same path as Telegram and `chela merge-approve`, and
re-check the head, CI, mergeability and expiry first; a request that cannot be approved says
why instead of showing the button. It updates live. Settings → Notifications has an opt-in
browser notification for new requests (off by default). A dashboard decision is recorded as
`decided_by: dashboard`. Set `CHELA_DASHBOARD_URL` to the address you browse the dashboard at,
so `chela merge --override` prints a link that works for you.

## Update

Settings → General → **Update** does what `chela update` does: pulls the checkout, re-syncs
dependencies and restarts the running `chela-*` services (or, with nothing to pull, only the
ones running stale code). A dirty tree or a diverged branch refuses. A dispatched run in
flight does not block it: agents and judges outlive the restart, and the daemon's restart
waits for its current tick (at most 60 s). If the services' freshness cannot be read it
answers 503 instead of "up to date". `GET /api/update/status` reports the same state
read-only.

## Usage view

Settings → Cost has a **Cost | Usage** toggle. Usage shows the 5h and 7d limit bars from the
freshest statusLine data, with a %/h burn rate and whether it reaches 100% before the reset.
Missing or stale data reads as unknown, never 0%. Below them are the top token consumers over
the last 30 minutes (across UTC midnight too) and today (UTC), read from **every** Claude Code transcript, including
judges, subagents and background sessions. Each row has its requests, token kinds and
cache-hit rate. On WSL it also reads the Windows-side transcripts; set `usage_extra_roots` in
`~/.chela/config.json` or `CHELA_USAGE_EXTRA_ROOTS` (`[]` for none) to change the extra roots.

The limit bars need the statusLine hook: `uv run chela install-statusline --write`.
