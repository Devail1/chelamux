# Handing a chela session to Claude Desktop and back: spike (CMX-391)

> **Verdict: CONDITIONAL GO on macOS. NO-GO on WSL.** This is a static read of Claude Code
> 2.1.327 plus the official docs. Five steps on the org Mac (§6) decide the rest.
>
> * **Handoff: clean.** `/desktop`, typed inside the live session, saves the transcript, opens
>   the Desktop app on the **same session id** and **exits the CLI**. `claude --desktop
>   --resume <id>` **refuses** a session that another process holds, whether interactive or
>   in the background. So Claude Code itself prevents two writers in the CLI → Desktop
>   direction.
> * **Return: clean per the docs.** "Desktop continues the same session rather than a copy,
>   so `claude --resume` in the terminal still finds it afterwards."
> * **Double-writer: only half guarded.** The static read found no Desktop → CLI guard: a plain
>   foreground `claude --resume <id>` while Desktop still has the session open showed no
>   refusal. **chela's "Bring back" must do the check itself** (§7). That is a requirement of
>   the follow-up, not a blocker.
> * **Remote Control policy: very probably not involved.** The `--desktop` code path never
>   reads `disableRemoteControl` or the org's Remote Control toggle. It opens a local
>   `claude://resume?session=<id>` URL through the operating system. Different policies can
>   block it (§1). Whether one of them is set on the org Mac is **unknown** until §6 step 1
>   runs.
> * **Hooks: they probably fire, but chela will hear only some of them.** The docs say hooks fire
>   in Desktop sessions. chela's `SessionStart` and tool hooks are gated on `$CHELA_WID`, which a
>   Desktop-spawned process does not have. `UserPromptSubmit` and `Stop` are ungated `http` hooks
>   and would still post, if chela runs on that Mac. A weaker "in Desktop" status is a valid
>   fallback, so this does not block the feature.
>
> **Not measured here.** Nothing in this spike ran a Claude session. chela's own nested-Claude
> gate (CMX-190, `chela/mergegate.py` `claude_launch`) denies every `claude` invocation from a
> dispatched run, `claude --help` included. Its allowlist is `--version`, `plugin
> test|validate` and `mcp list`. The CLI facts below come from the strings of the installed
> binary instead. That is stronger than `--help` on behaviour and weaker than a run. The
> throwaway-session measurements moved to §6, for Liav on the Mac, which is the only platform
> where they can run anyway (§5).

Evidence key: **[bin]** is the installed Claude Code 2.1.327 binary (Linux x64 build, built
2026-11-17), read with `strings` and never executed. **[docs]** is code.claude.com, fetched
2026-11-19. **[repo]** is this tree.

## 1. Is `--desktop` allowed when the org disables Remote Control?

**Probably yes. The CLI side does not consult Remote Control at all. Whether the org Mac
allows it is unknown until §6 step 1.**

The settings that disable Remote Control:

| Setting | Where | What it does | Source |
|---|---|---|---|
| `disableRemoteControl` | settings file, any scope including managed | "Turn off Remote Control everywhere it can start". Refusal text: *"Remote Control is disabled by your organization's policy (managed setting `disableRemoteControl`)."* | [docs] `settings-reference#settings-index`, `remote-control#connection-and-security`; [bin] |
| Org **Remote Control** toggle | Claude Code admin console | "enable or disable Remote Control for your organization". Refusal text: *"Remote Control is turned off for your organization"* | [docs] `desktop#admin-console-controls`, `remote-control#requirements`; [bin] |
| `remoteControlAtStartup: false` | managed settings | stops auto-connect only. It does **not** disable Remote Control | [docs] |

**What `--desktop` actually does.** This is `launchDesktopFromCli` [bin], in order:

1. It refuses non-interactive use (`--print`, `--sdk-url`, redirected output), any other flag,
   a prompt argument, piped stdin and a restricted environment (`CLAUDE_CODE_RESTRICTED`,
   `…_RESTRICT_PERSONAL_CONFIG`, `…_SAFE_MODE`, `…_SIMPLE`).
2. It refuses an unsupported platform: *"--desktop isn't available on this platform. It works
   on macOS and Windows (x64)."*
3. It requires **a Claude account sign-in**: *"--desktop requires signing in with a Claude
   account."* `forceLoginMethod: console` blocks it, including from managed settings: *"Managed
   settings set forceLoginMethod to console … Ask your administrator to change it."* [docs]:
   "not available with API key authentication or on Amazon Bedrock, Google Cloud's Agent
   Platform, or Microsoft Foundry."
4. It refuses a session that another process holds (§2).
5. It checks that Desktop is installed and at least version **1.1.9669**. Then it opens
   `claude://resume?session=<id>`, or `claude://code/new?folder=<dir>` for a bare `--desktop`,
   through the OS URL handler. On this Linux build that is `xdg-open`; the macOS build uses its
   own opener.
6. It prints `Opening session <id> in Claude Desktop` and **exits 0**.

None of these steps reads `disableRemoteControl` or the org Remote Control toggle, and none of
them opens a network channel. The handoff is a **local OS deep link**. The receiving app then
runs the session as a **local** Code session ([docs] `desktop#environment-configuration`:
"Local: runs on your machine"). The docs never connect `--desktop` or `/desktop` to Remote
Control. The only link they draw is the reverse one: a session that already had Remote Control
**on** reattaches to its claude.ai session when Desktop resumes it
(`remote-control#resume-sessions-after-stopping-the-server`). On the org Mac Remote Control
is off, so that path does not arise.

**Policies that *can* block it.** These are the ones to look for on the org Mac:

| Policy | Effect |
|---|---|
| Desktop `isClaudeCodeForDesktopEnabled` ("Allow Code") [bin] / "enabling or disabling the Claude Code feature" [docs `desktop#device-management-policies`] | no Code tab, so nothing to open into |
| Desktop `disableDesktopLocalSessions` [docs `desktop#managed-settings`] | "turn off Code sessions that run on the device", which is exactly what the handoff creates |
| A Desktop policy titled "Disable claude:// deep-link handling" ("Stop external apps and websites from opening Claude Desktop via claude:// links") [bin]. Its key name is not confirmed; the policy description refers to `disableDeepLinkRegistration` for third-party deployments | the `open` succeeds and the app ignores the link |
| `forceLoginMethod: console`, or a non-subscription auth (API key, Bedrock, Vertex, Foundry) | `--desktop` refuses before opening anything |

Desktop policy lives in `com.anthropic.claudefordesktop` (macOS) [docs]. Claude Code managed
settings live in the managed-settings file. §6 step 1 reads both.

## 2. Handoff semantics: what happens to the terminal process?

There are **two entry points, and they behave differently**:

| Entry | Terminal process | Session live elsewhere | Source |
|---|---|---|---|
| `/desktop` (alias `/app`) inside the running session | "Saving session…" (flushes the transcript), opens the deep link, prints *"Session transferred to Claude Desktop"*, then **exits the CLI** (exit 0, about 500 ms later) | n/a: it *is* the holder | [bin]; [docs] `desktop#coming-from-the-cli`: "saves your session and opens it in the desktop app, then exits the CLI" |
| `claude --desktop --resume <id>` from a shell | a short-lived launcher: it opens the link and exits 0. **No session ever runs in the terminal** | **refused**, exit 1: *"Session \<id\> is open in another terminal. Run /desktop there instead."* A background holder gets the `claude attach` / `claude stop` hint | [bin] `launchDesktopFromCli`; [docs] "doesn't move a session that is open in another terminal or still running in the background" |

Further detail from [bin]:

* `--desktop --resume` takes **only a full session UUID**: *"takes a session ID, not a name
  or path."* A missing id gets its own error.
* The live check reads Claude Code's registry (`<config>/sessions/<pid>.json`, every live pid
  for that `sessionId`). If the registry is unreadable it **fails closed**: *"Couldn't check
  whether that session is in use."*

**Can one id be live in two places?** In the CLI → Desktop direction, no: both entry points
refuse to start a second writer. In the Desktop → CLI direction the binary showed **no
guard**. The `open-elsewhere` → "started a copy" logic exists only on the `--bg --resume`
path. A plain foreground `claude --resume <id>` against a session Desktop holds is unproven,
and §6 step 4 measures it. Two processes appending to one JSONL is the corruption risk the
brief names. chela must therefore treat this direction as **unguarded** until measured, and
guard it itself (§7).

**Consequence for chela.** The clean handoff is **`/desktop` typed into the agent's own pane**:
it flushes first, and its own exit is the proof that the CLI let go. Running `claude --desktop
--resume <id>` from a second shell while the agent runs is refused by design. Doing it after
killing the agent skips the flush.

## 3. Coming back

[docs] `desktop#coming-from-the-cli`: *"Desktop continues the same session rather than a copy,
so `claude --resume` in the terminal still finds it afterwards."* Desktop's local sessions read
the same `~/.claude/settings.json` and `~/.claude.json` (`desktop#shared-configuration`). So
`claude --resume <id>` in tmux should continue **with the Desktop turns included**, under the
same id.

Not stated, and measured by §6 step 5:

* whether Desktop writes the turns to the same `~/.claude/projects/<cwd>/<id>.jsonl`;
* whether anything made only in Desktop is lost on the way back. Candidates: Desktop-only
  UI state such as previews, and a model or permission mode chosen in the app. Tool results
  are in the transcript, so they are not candidates.

A session that was archived in the meantime gets *"Every session you can continue here is
archived. Run `claude --desktop --resume <id>` to open the most recent one."* [bin].

## 4. Hooks while the session is in Desktop

[docs] `hooks`: *"Claude Code fires the same hook events wherever it runs: sessions in the
terminal, IDE extensions, the Desktop app, and cloud sessions."* Plugins that are enabled
user-wide are available in Desktop's local sessions (`desktop#install-plugins`), but **not** in
WSL sessions. So the hooks **fire**. Whether chela **hears** them depends on how each one is
wired in `plugin/hooks/hooks.json` [repo]:

| chela hook | Transport | Gated on `$CHELA_WID` | Reaches chela from Desktop? |
|---|---|---|---|
| `SessionStart` | `command` | yes | **no**: Desktop's process is not spawned by chela, so `CHELA_WID` is unset and the `&&` short-circuits |
| `PreToolUse` / `PostToolUse` / `PermissionRequest` / `PermissionDenied` | `command` | yes | **no**, same reason |
| `UserPromptSubmit`, `Stop`, `SessionEnd`, `Notification` | `http` → `127.0.0.1:5001` | no | **yes**, *if* the chela plugin is enabled on that Mac **and** chela's daemon listens there. `chela/hooks.py` correlates them by `session_id` / origin directory, not by pane |
| merge gate (`mergegate.py`) | `command` | no | runs; it is irrelevant here |

**Result:** busy (`UserPromptSubmit`) and idle (`Stop`) can plausibly drive an "in Desktop ·
busy/idle" badge. `SessionStart` cannot announce the handoff, but chela does not need it to:
chela initiated the handoff, so it already knows. If the hooks do not arrive, the fallback is
the registry's own `status` field in `<config>/sessions/<pid>.json`, which chela already reads
(`chela/sessions.py` `registry_entry`), *if* Desktop's session process registers there. The
weakest fallback is a plain "in Desktop" that chela records when it hands off. **The feature
can go ahead on any of the three.** §6 steps 3–4 tell which one is available.

## 5. This machine (WSL + the Windows Desktop app), and the Mac

**WSL: no.** The Linux build compiles the feature out [bin]: the platform predicate is
`function nts(){return!1}`, so `--desktop` exits 1 with *"--desktop isn't available on this
platform. It works on macOS and Windows (x64)."* `/desktop` is hidden. Even with the
predicate open, a WSL session runs on the Linux side and the Windows app runs its sessions on
the Windows side. The docs list Desktop `/resume` as "not in SSH, WSL, or cloud sessions" and
plugins as unavailable in WSL sessions. On this box chela-on-WSL cannot hand a session to the
Windows app. Liav can confirm it with a command that opens nothing:

```bash
cd "$(mktemp -d)" && claude --desktop; echo "exit=$?"
# expected: --desktop isn't available on this platform. It works on macOS and Windows (x64).
#           exit=1
```

**Mac: expected to work.** The requirements are Claude Code ≥ 2.1.285 [docs], Desktop ≥
1.1.9669 [bin], a Claude subscription sign-in, and none of the §1 blocking policies. **chela
itself has to run on that Mac** for the follow-up to matter: today it runs on this WSL box,
and a Desktop session on the Mac lives in the Mac's `~/.claude`.

## 6. The manual check on the org Mac (5 steps, throwaway session only)

Run these in a plain terminal, not in a chela-managed session. Paste back every line marked ⟶.

1. **Versions, sign-in and policy.**
   ```bash
   claude --version; claude auth status                       # ⟶ ≥2.1.285; a Claude account, not Console/API key
   defaults read /Applications/Claude.app/Contents/Info.plist CFBundleShortVersionString   # ⟶ ≥1.1.9669
   defaults read com.anthropic.claudefordesktop 2>&1           # ⟶ look for Code / local-session / deep-link keys
   cat "/Library/Application Support/ClaudeCode/managed-settings.json" 2>&1   # ⟶ disableRemoteControl, forceLoginMethod
   ```
2. **A throwaway session with a hook probe, then the handoff.**
   ```bash
   mkdir -p ~/desktop-spike/.claude && cd ~/desktop-spike
   cat > .claude/settings.json <<'EOF'
   {"hooks": {
     "SessionStart":     [{"hooks": [{"type": "command", "command": "echo \"$(date +%T) SessionStart wid=${CHELA_WID:-unset}\" >> ~/desktop-spike/hooks.log"}]}],
     "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo \"$(date +%T) UserPromptSubmit\" >> ~/desktop-spike/hooks.log"}]}],
     "Stop":             [{"hooks": [{"type": "command", "command": "echo \"$(date +%T) Stop\" >> ~/desktop-spike/hooks.log"}]}]}}
   EOF
   SID=$(uuidgen | tr A-Z a-z); echo "$SID"                     # ⟶ the id
   claude --session-id "$SID"
   ```
   Inside: send `reply with exactly TERM-1`, then type `/desktop`. ⟶ the last lines the
   terminal printed, the shell's `echo $?`, and whether Desktop opened **on that
   conversation**. If `/desktop` is refused, also try `claude --desktop --resume "$SID"; echo
   "exit=$?"` ⟶. **A refusal here, or Desktop ignoring the link, is the org-policy answer.**
3. **Desktop holds it: registry and transcript.** In Desktop, send `reply with exactly
   DESKTOP-1`. Then, in the terminal:
   ```bash
   grep -l "$SID" ~/.claude/sessions/*.json | xargs cat                # ⟶ kind / entrypoint / status / tmux (or nothing)
   grep -c 'DESKTOP-1' ~/.claude/projects/*/"$SID".jsonl               # ⟶ ≥1 means same transcript
   cat ~/desktop-spike/hooks.log                                       # ⟶ did UserPromptSubmit/Stop fire for DESKTOP-1?
   ```
4. **Double-writer probe, with Desktop still open on the session.** Send nothing, and exit
   right away:
   ```bash
   claude --desktop --resume "$SID"; echo "exit=$?"     # ⟶ expected: refused (open elsewhere)
   claude --resume "$SID"                               # ⟶ warning? refusal? "started a copy"? then /exit without typing
   ```
5. **Coming back.** Quit Desktop (⌘Q), then:
   ```bash
   claude --resume "$SID"
   ```
   Ask `what were your last two replies, verbatim?` ⟶ the answer (expected: TERM-1 and
   DESKTOP-1), then `/exit` and `tail -3 ~/desktop-spike/hooks.log` ⟶. Delete
   `~/desktop-spike` afterwards.

**Reading the results.** Steps 2 and 5 clean → **GO**. Step 4 refuses or forks a copy → Claude
Code guards Desktop → CLI too, and §7's own check becomes belt-and-braces. Step 4 resumes the
**same** id silently → chela's check is mandatory and its "Bring back" must not skip it. Step
3's registry output picks the status source in §4.

## 7. Design sketch for the follow-up (not built)

**`chela desktop @N`** and the row action **"Open in Desktop"**:

1. Resolve `@N` → `sessionId` from Claude Code's own registry (`sessions.registry_entry` for
   the pane's pid). Never from cwd.
2. Refuse: a dispatched run (the judge contract needs its pane), the orchestrator, a
   non-macOS/Windows host (one probe of the platform message, cached), and a busy agent. Ask
   for idle first, because `/desktop` flushes but the brief does not say whether it waits for
   an in-flight turn.
3. Send `/desktop` into the pane through chela's normal pane-input path. Wait for the pane's
   Claude pid to exit and for *"Session transferred to Claude Desktop"*. On a timeout or an
   error text (`Didn't open Claude Desktop…`, `Claude Desktop isn't installed`), leave
   everything as it was and surface the text.
4. Record `{wid, sessionId, handed_at}` as **in Desktop**. Keep the window, parked at a shell
   that says so, so the row and its Telegram topic survive.

**Status while away:** `UserPromptSubmit`/`Stop` → busy/idle where they arrive (§4).
Otherwise read the registry `status` by `sessionId`. Otherwise show a plain "in Desktop".

**"Bring back to chela"** (row action, `chela desktop --back @N`):

1. **Guard, mandatory:** find any live registry entry for the `sessionId`, with
   `procStart` validated as `registry_entry` does. If one exists, **refuse** with "close it
   in Claude Desktop first". An unreadable registry also refuses (fail closed). Claude Code
   does not provably do this check for us (§2).
2. Relaunch in the parked window the way chela launches any agent, with `CHELA_WID` exported
   and `claude --resume <sessionId>`, so every hook comes back.

**Tests the follow-up owes:** the refusal table above (each kind of holder, an unreadable
registry), the exact `/desktop` keystrokes on the success path, the timeout path leaving
state untouched, and the back-guard driven by both a live entry and a stale (recycled-pid)
entry.

## Go / no-go

| Criterion | Status |
|---|---|
| Clean handoff | ✅ by construction ([bin] + [docs]): `/desktop` flushes and exits |
| No double-writer | ⚠️ CLI → Desktop guarded by Claude Code. Desktop → CLI **unguarded as far as found**, so chela must guard it (§7). §6 step 4 measures it |
| Clean return | ✅ per [docs]. §6 step 5 confirms it |
| Allowed with Remote Control off | ✅ expected: no Remote Control check on the path. **The test is §6 steps 1–2.** Blockers are Desktop's Code / local-session / deep-link policies and a non-subscription login, not Remote Control |
| Hooks into chela | partial: `UserPromptSubmit`/`Stop` only, and only with chela on the Mac. A weaker "in Desktop" status is acceptable |

**CONDITIONAL GO for macOS: build the follow-up once §6 steps 2 and 5 pass on the org Mac.
NO-GO on WSL.**
