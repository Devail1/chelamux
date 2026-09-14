# The sandbox boundary for dispatched agents

Step 1 of [#502](https://github.com/Devail1/chelamux/issues/502): *enumerate what a dispatched
agent, a rework and a judge legitimately write and reach, then derive the allowlists.*

⛔ **This document does not turn anything on.** It is the enumeration #502 asks for, plus the
measurements that decide the config. Two of its findings say the config alone cannot get there.

Everything below labelled **measured** was run on a real host — Claude Code 2.1.270, WSL2,
bubblewrap 0.9.0, socat present — against a real detached worktree of this repo at the
0.12.2 release commit, under `claude -p --settings <file> --strict-mcp-config`. Each negative
result has a matched unsandboxed control, because a failure inside a sandbox looks identical
whether the sandbox caused it or not. The raw probe output is in the appendix.

---

## 1. The three principals

`_launch_agent` is the only spawn path, and all three go through it. They differ in cwd, in what
they are allowed to do to the world, and — this turns out to matter — in whether they push.

| principal | cwd | writes code? | pushes / opens a PR? | writes the run row? |
|---|---|---|---|---|
| **coding agent** (first dispatch) | `<workspace.root>/<task>` — a linked git worktree on its own branch | yes | yes | yes, via `chela task-finished` |
| **rework agent** (re-spawn after a verdict) | the *same* worktree, relaunched | yes | yes (force-push to the same branch) | yes, via `task-finished` / `rework-disputed` |
| **judge** | a *throwaway detached* worktree at the PR head | only the mutations chela applies and restores | ⛔ never | judge columns only, via `chela judge` |

The judge is the odd one out in both directions: it never pushes and never touches the branch, but
it is the one that must run `judge.test_cmd` green before any mutation means anything. That asymmetry
drives the recommendation in §6.

## 2. What the stock sandbox already gives us — measured

Before adding a single `allowWrite`, the defaults cover more than expected and less than needed.

| probe | result | consequence |
|---|---|---|
| write inside cwd | allowed | the worktree is the agent's, as intended |
| **`git commit` in a linked worktree** | **allowed** | the sandbox grants writes to the main repo's shared `.git` for linked worktrees. `git add`/`commit`/`push` need no configuration |
| **write `<main repo>/.git/hooks/pre-commit`** | **denied** — `Read-only file system` | the worst filesystem vector is closed by default: an agent cannot plant a hook that runs unsandboxed on the operator's next git command |
| write `~/` , `~/.chela/` , `~/.cache/uv` | denied | see §3 — two of these are legitimately needed |
| read `~/.config/gh/hosts.yml` | **allowed, in cleartext** | reads are unrestricted by default. The GitHub token is readable by any sandboxed command unless listed under `sandbox.credentials` |
| tmux control socket | denied — `Operation not permitted` | see §5 |
| outbound to a non-allowlisted host | denied — `CONNECT tunnel failed, response 403`, reported to the agent as a named sandbox violation | the agent is told what was blocked, so it can adapt rather than hang |
| outbound to an allowlisted host | 200 | |
| `$TMPDIR` | set to the session temp dir, writable | `tempfile.mkdtemp` in `tests/conftest.py` and pytest's `tmp_path` both land there |
| the `--settings` file itself, from inside | denied | an agent cannot widen its own boundary — **as long as the file is not under a granted path**. See §6.2 |

⚠️ **`/proc/self/cgroup` is not a sandbox marker.** Measured: inside the sandbox it still reports the
outer tmux scope. The only reliable evidence the sandbox is on is a denied write or a reported
violation — which is what the guard in §7 asserts.

## 3. The write set

| path | who needs it | why | default |
|---|---|---|---|
| the worktree | all three | source edits, `.venv/`, `.pytest_cache/`, `__pycache__/`, the experiments JSON | ✅ cwd |
| main repo's shared `.git` (refs, objects, index, logs) | all three | `git add`, `commit`, `push`, worktree bookkeeping | ✅ automatic for linked worktrees |
| session temp dir | all three | conftest's session sandbox dir, `tmp_path`, `PM2_HOME`, shell snapshots | ✅ |
| `~/.cache/uv` | all three | `uv sync` / `uv run` package cache | ❌ **`allowWrite`** |
| `~/.local/share/uv` | all three | uv-managed interpreters | ❌ **`allowWrite`** |
| `~/.chela/` — the whole directory | coding + rework | `chela task-finished` writes `scheduler.db` (**plus `-wal`/`-shm` siblings, so the grant must be on the directory, not the file**), and any `chela` subcommand appends `events.jsonl` and rewrites `inbox.json` / `roster.json` / `session-ids.json` | ❌ **and this is blocker B1, §5** |
| `<workspace.root>/.npm-shared/` | nobody inside the sandbox | `hooks.before_run` builds it, and hooks run in the **dispatcher's** process, outside any sandbox | n/a |

⛔ **Nothing in the agent's job requires writing `~/.claude`.** `mark_awaiting_review` *reads*
`~/.claude/projects/**` to recover the PR URL from the transcript, and reads are already allowed.

## 4. The egress set

| host | who | why |
|---|---|---|
| `api.anthropic.com` | the Claude Code **process**, which is not itself sandboxed | model traffic. Measured: denied to sandboxed *bash* while the session itself kept working — confirming the sandbox's scope is Bash subprocesses only |
| `github.com` | coding, rework | `git push`, `git fetch` |
| `api.github.com` | coding, rework, judge | `gh pr create` / `view` / `list`, and `_read_pr_status` |
| `pypi.org`, `files.pythonhosted.org` | all three | `uv sync`, and any `uv run` that finds the venv stale |
| `registry.npmjs.org` | `before_run` (outside the sandbox); an agent only if it re-runs the install | jsdom |
| **`api.telegram.org`** | **the test suite** | ⚠️ measured: three outbound attempts during one suite run, blocked by the sandbox. Unsandboxed they leave the box. Worth its own issue — a unit suite should not be dialling a real API |

## 5. Three blockers config cannot solve

### B1 — the completion protocol requires the control plane

`chela task-finished` is Done Criteria step 6, i.e. mandatory. It does two privileged things
*in the agent's own process*: it `UPDATE`s the run row in `~/.chela/scheduler.db`, and it kills
the agent's tmux window through the shared tmux socket.

Granting both makes the sandbox cosmetic:

- **`~/.chela` write** ⇒ the agent can also rewrite `chela.env`, `config.json`, `daemon.json`,
  `telegram-bindings.json`, every other run's row — and, if the sandbox settings file were kept
  there, its own boundary. Claude Code applies filesystem-list edits to the *running* session, so
  that last one is not even a next-run problem.
- **tmux socket** ⇒ `tmux send-keys` into *any* window, including the operator's own. That path
  delivers text with no `from` attribution and therefore no cross-session banner: a peer message
  that merely reads like an instruction arrives wearing the operator's authority. The sandboxing
  docs single out `allowUnixSockets` as a privilege-escalation vector; tmux is close to the worst
  case of it.

⇒ **The completion hop has to stop being the agent's write.** The agent drops a request — a file
in its own worktree, which it can already write — and the daemon, which is unsandboxed and already
polling, performs the transition and the window kill. This is the same shape as Symphony's SPEC §15
("host-side tracker secret environment variables SHOULD NOT be inherited by the coding-agent child
process"): keep the privileged action on the host side of the boundary.

### B2 — credential masking keeps `gh` working and breaks `git push`

#502 proposes masking the `gh` token via `sandbox.credentials.files`, and the docs' worked example
is literally that file. It half works, and the half that fails is the one the workflow depends on.

**Measured**, with `github.com` and `api.github.com` in both `network.allowedDomains` and the entry's
`injectHosts`, and `network.tlsTerminate` set:

| command | result |
|---|---|
| `cat ~/.config/gh/hosts.yml` | shows `fake_value_<uuid>` — the mask is working |
| `gh api user` | ✅ returns the real login |
| `gh pr list` | ✅ |
| **`git push --dry-run`** | ❌ `remote: Invalid username or token. Password authentication is not supported for Git operations.` |

Control: the identical `git push --dry-run`, same worktree, same remote, **unsandboxed**, succeeds.
A second control rules out a permissions explanation: an inaccessible repo fails with
`Repository not found`, a different error entirely.

Mechanism, measured by tracing both clients:

```
git:  Send header: Authorization: Basic <redacted>      # base64(user:token)
gh:   > Authorization: token ████████████████████       # the token, verbatim
```

The proxy substitutes the sentinel **as a substring** of headers and bodies. `gh` sends the token
verbatim, so substitution finds it. git base64-encodes it, so the sentinel is not present verbatim,
nothing is substituted, and the sentinel itself reaches GitHub. `maskDuplicates` does not help — it
also matches raw substrings.

Options, in the order I'd rank them:

1. **Move the push and the PR open to the daemon**, alongside B1. The agent commits locally; the
   daemon pushes the branch and opens the PR. The token then never enters the sandbox at all, which
   is strictly stronger than masking it, and it collapses B1 and B2 into one change.
2. **Don't mask; rely on the domain allowlist.** Weaker, and the docs say so: a broad `github.com`
   allow is itself an exfiltration path, since the proxy decides from the client-supplied hostname
   without inspecting TLS.
3. **File it upstream** — base64-aware substitution for HTTP Basic would fix this class for every
   git-over-HTTPS user. Worth doing regardless of which of 1/2 we take.

### B3 — the judge's own suite goes red inside the boundary

This is the CMX-80 failure mode #502 warns about, and it reproduces. Full suite, same worktree,
same commit, once with and once without the sandbox:

| | passed | failed | errors |
|---|---|---|---|
| unsandboxed control | **3983** | 1 (a known `CHELA_RESTORE_RESUME` env artifact of running it by hand; `judge._no_color_env` strips it) | **0** |
| sandboxed | **3856** | 18 | **110** |

≈128 tests lost. Every named module is a live-fleet one — `test_runtime_truth.py`,
`test_terminals_selfheal.py`, `test_epoch_live.py`, `test_telegram_new_launch_bind.py` — i.e. tests
that open the tmux socket, plus the `api.telegram.org` egress from §4.

`judge.test_cmd` **is** this suite. A judge that cannot get a green baseline judges nothing and
returns `cannot_verify` on every PR — exactly the two-month outage CMX-80 caused the first time.

## 6. What I'd propose

### 6.1 Sandbox the coding and rework agents. Do not sandbox the judge — yet.

> ## ✅ DECIDED 2026-09-14 — option (a): ship it for coding + rework, defer the judge.
>
> The owner's call, taken as an explicit risk acceptance rather than an oversight: **the judge stays
> an unsandboxed LLM agent on this machine.** The reasoning is where the exposure actually sits — a
> coding agent takes an arbitrary task description and pushes to a public repo; the judge runs a
> fixed command it cannot choose, against a throwaway tree, and never pushes.
>
> ⛔ Making the ~128 live-fleet tests socket-free is **follow-up work, not a blocker** on shipping
> the boundary for the two roles that need it.

The two roles have genuinely different threat profiles. A coding agent takes an arbitrary task
description, writes code, and pushes it to a public repo: it is the one that benefits. The judge
runs a fixed command from a WORKFLOW.md it cannot choose, applies mutations chela generates, in a
throwaway tree, and never pushes. Sandboxing it costs 128 tests and buys the least.

⚠️ This is a **deferral, not a dismissal** — the judge is still an LLM agent on this box. The way to
close it is to make those ~128 tests socket-free (they mostly want a scratch tmux socket, which
`test_epoch_live.py::test_the_scratch_socket_never_touches_the_real_fleet` shows the suite already
knows how to do), not to grant the socket.

### 6.2 Carry the config on `--settings`, not in any settings file that already exists

- ⛔ **Not `~/.claude/settings.json`** — that is the operator's own file, and it would sandbox their
  interactive sessions too.
- ⛔ **Not the repo's `.claude/settings.json`** — Claude Code deliberately ignores `mask` entries,
  `tlsTerminate`, `strictAllowlist` and `filesystem.disabled` from project settings, which is most
  of what we need; and a dispatched agent can edit that file.
- ✅ **A chela-owned file passed as `--settings`**, appended in `_launch_agent` next to the existing
  `--strict-mcp-config` and the messaging-socket argument. `--settings` is one of the three sources
  that may carry the restricted keys.
- ⚠️ **Where that file lives is load-bearing.** It must not sit under any granted path. Measured: it
  is read-only from inside the sandbox when it sits outside the allow set — but if B1 were solved by
  granting `~/.chela`, a settings file there would become writable and the boundary self-editable.
  `~/.claude/` is a reasonable home precisely because the sandbox protects that directory by
  construction.

### 6.3 The starting config, once B1 and B2 are addressed

```jsonc
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    // ⛔ load-bearing: without it Claude retries a blocked command with
    // dangerouslyDisableSandbox and the boundary is advisory, not enforced.
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "allowWrite": ["~/.cache/uv", "~/.local/share/uv"]
    },
    "network": {
      // ⛔ also load-bearing: without it an unlisted host PROMPTS, and a dispatched
      // agent has nobody to answer — the dispatcher finds it hung.
      "strictAllowlist": true,
      "allowedDomains": [
        "github.com", "*.github.com",
        "pypi.org", "files.pythonhosted.org",
        "registry.npmjs.org"
      ]
    },
    "credentials": {
      "files": [
        { "path": "~/.config/gh/hosts.yml", "mode": "deny" }
      ]
    }
  }
}
```

`deny` rather than `mask` on the token file follows option 1 of B2: once the daemon owns the push,
no sandboxed command needs the token, and `deny` is simpler and stronger than a mask whose
substitution only covers one of the two clients.

### 6.4 Order of work

1. **Move `task-finished`'s two privileged effects to the daemon** (B1). Independently valuable —
   it also removes the agent's ability to corrupt other runs' rows, sandbox or no sandbox.
2. **Move push + PR-open to the daemon** (B2), or accept option 2 with the risk written down.
3. Ship 6.3 behind a per-workflow flag, coding + rework roles only.
4. Trial on one workflow; then default it on.
5. Separately: make the ~128 live-fleet tests socket-free, then extend the boundary to the judge.

## 7. The guard

#502's Guard asks for both halves, and the accept case is the one a suppress-only test would miss.

- **Negative control** — a sandboxed `touch` outside the granted set must fail with
  `Read-only file system`, and the run must report a sandbox violation. ⛔ Asserting only that the
  settings file contains `"enabled": true` proves nothing: `/proc/self/cgroup` shows the sandbox is
  invisible from inside, and `allowUnsandboxedCommands` defaulting to `true` would let every blocked
  command quietly retry outside it.
- **Accept case** — one complete dispatch under the boundary: worktree write, `uv sync`, suite green,
  commit, push, PR open, run row transitioned. Anything less certifies the deny half only.

---

## Appendix — measurements

Host: WSL2, Claude Code 2.1.270, bubblewrap 0.9.0, socat present. Probes run via
`claude -p --settings <file> --strict-mcp-config`, in a linked git worktree of a throwaway repo
(probes 1–3) and in a detached worktree of this repo at the 0.12.2 commit (the suite runs).

**Probe 1 — stock boundary, no `allowWrite`, allowlist `[pypi.org, files.pythonhosted.org]`**

```
P1  write ./inside.txt                     OK
P2  git commit (linked worktree)           OK
P3  touch ~/.chela/PROBE                    Read-only file system
P4  head ~/.config/gh/hosts.yml            READ_ALLOWED  (token in cleartext)
P5  tmux -S /tmp/tmux-*/default list-windows   error connecting ... (Operation not permitted)
P6  touch ~/PROBE                          Read-only file system
P7  touch ~/.cache/uv/PROBE                Read-only file system
P8  curl https://api.github.com/           curl (56) CONNECT tunnel failed, response 403
    → violation: deny network-outbound api.github.com:443 (host is not on the allow list)
P9  curl https://pypi.org/simple/          200
P10 TMPDIR=<session temp dir>              writable
P11 write <main repo>/.git/hooks/pre-commit   Read-only file system
P12 /proc/self/cgroup                      outer tmux scope — NOT a sandbox marker
```

**Probe 2 — masking `~/.config/gh/hosts.yml`, `tlsTerminate` on, allowlist `[*.github.com, github.com]`,
`allowWrite: [~/.cache/uv, ~/.chela]`**

```
Q1  oauth_token in hosts.yml               fake_value_f3bb8ba4-1366…   (mask working)
Q2  gh api user -q .login                  <real login>                 (injection working)
Q3  git ls-remote <inaccessible repo>      Invalid username or token…
Q4  git ls-remote <public repo>            <sha> HEAD                   (unauthenticated path)
Q5  touch ~/.cache/uv/PROBE                OK                           (allowWrite works)
Q6  sqlite3 ~/.chela/PROBE.db + WAL        OK                           (directory grant works)
Q7  overwrite the --settings file          Read-only file system
Q8  curl https://api.anthropic.com/        denied to bash; session unaffected
```

**Probe 3 — the B2 control pair**

```
R1  git push --dry-run  (sandboxed, masked)   Invalid username or token. Password
                                              authentication is not supported for Git operations.
    same command, unsandboxed                 * [new branch] probe -> zz-sandbox-probe   (dry run; no ref created)
    an inaccessible repo, unsandboxed         Repository not found      (a different failure)
R2  gh pr list (sandboxed, masked)            OK
```

**Suite runs** — detached worktree at the 0.12.2 commit,
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, `before_run` executed outside the sandbox first:

```
unsandboxed:  1 failed, 3983 passed, 1 skipped        in 106s
sandboxed:   18 failed, 3856 passed, 1 skipped, 110 errors  in 165s
             + 3 × deny network-outbound api.telegram.org:443
```
