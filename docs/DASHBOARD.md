# The dashboard

`uv run chela dashboard` serves the web UI on `http://127.0.0.1:5001` (`--host` / `--port`,
or `CHELA_DASH_HOST` / `CHELA_DASHBOARD_PORT`). It needs the `dashboard` extra, which
`uv sync --all-extras` installs. It binds loopback and has no login of its own: reach it
remotely through a tailnet or an SSH tunnel ([the security notes](https://chela.pages.dev/docs#security)).

There are two views and a Settings panel:

- **Wall** — every agent's live terminal, each pane marked *working*, *needs you*, *PR open*
  or *idle*, with a context bar, its tmux id (`@N`, click to copy), and file drop / paste into
  the session's `uploads/` folder.
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
  agent and judge windows fold into it, and while the judge's battery runs the row reads
  `⚖️ testing · 3/6 · 15m`.
- **The orchestrator's row carries an `orchestrator` badge.** The inbox icon in the sidebar
  header jumps to it (focusing its pane and expanding its group) and opens the decisions log.
  It is not pinned: it stays in its folder group.
- **The VIEW menu** (the sliders button on the Sessions header) picks **Status** (Active / All /
  Archived), **Environment** (Interactive, Dispatched agents, Judges, Background sessions),
  **Last activity** (1d / 7d / 30d / All), **Group by** (Date, Folder, State, Custom groups,
  None) and **Sort by** (Last activity, Name, Created), plus *Show empty groups* and *Show PR
  status*. Choices are kept per browser. A filter never hides a session that is working or
  waiting on you, or the orchestrator.
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
- Archived rows sit in one collapsed **Archived (N)** section at the bottom.
- Archive state is kept by chela in `$CHELA_DIR/sidebar-archive.json`
  (`chela/sidebar_archive.py`), so every device shows the same set.

## Window names and Remote Control

A window chela opens (the dashboard launcher, Telegram `/new`, `chela spawn`, a resume) is
named after its folder: the cwd basename, or your login for the home directory, with a `-N`
suffix if taken. Claude Code starts with `--remote-control <that name>`, so claude.ai and the
Claude desktop show the same name. Turn that off in Settings → General → Remote Control, or
with `CHELA_REMOTE_CONTROL=false`. Dispatched agents and judges never get Remote Control.

chela renames a window only to resolve a duplicate (the newer one gets the `-N`). A rename
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

## Usage view

Settings → Cost has a **Cost | Usage** toggle. Usage shows the 5h and 7d limit bars from the
freshest statusLine data, with a %/h burn rate and whether it reaches 100% before the reset.
Missing or stale data reads as unknown, never 0%. Below them are the top token consumers over
the last 30 minutes and today (UTC), read from **every** Claude Code transcript, including
judges, subagents and background sessions. Each row has its requests, token kinds and
cache-hit rate. On WSL it also reads the Windows-side transcripts; set `usage_extra_roots` in
`~/.chela/config.json` or `CHELA_USAGE_EXTRA_ROOTS` (`[]` for none) to change the extra roots.

The limit bars need the statusLine hook: `uv run chela install-statusline --write`.
