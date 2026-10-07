"""⚖️🔒 The merge gate, enforced INSIDE every Claude session — a ``PreToolUse`` command hook.

``chela merge`` (:func:`chela.contract.merge`) refuses unless the judge is clean on the PR's
current head, CI is green and the PR is mergeable. But a session can simply run
``gh pr merge`` and skip every one of those checks: ``dev`` carries no branch protection,
and on 2026-09-28 a second orchestrator session merged five PRs that way — by discipline,
not enforcement. Every Claude session in the fleet loads chela's plugin, so a ``PreToolUse``
hook on ``Bash`` is the one place that sees EVERY agent's, judge's and orchestrator's
command before it runs. This file is that hook.

It DENIES (``hookSpecificOutput.permissionDecision: "deny"``) any command that would merge
into a chela workflow's repo:

* ``gh pr merge …`` — any PR in a repo with a chela workflow (``--auto`` included);
* ``gh api -X PUT repos/O/R/pulls/N/merge``, ``POST repos/O/R/merges`` into a protected
  base, ``PATCH``/``DELETE repos/O/R/git/refs/heads/<protected>``, and a GraphQL
  ``mergePullRequest`` / ``enablePullRequestAutoMerge`` mutation;
* ``git push`` whose destination is the workflow's base branch or a production branch
  (``main``/``master``/…), including ``--all``/``--mirror`` and a bare ``git push`` while
  that branch is checked out;
* a Claude session approving its OWN override (``chela merge-approve``, or a request to
  the dashboard's ``/override/`` route) — the operator's approval must come from a human;
* a Claude session approving a sandboxed guest's access request (``chela share-requests
  approve``, or a request to the dashboard's ``/api/share-requests/…/approve`` route,
  CMX-7) — same rule.

* 🧯 ``tmux kill-server`` / ``tmux kill-session`` aimed at the LIVE server (CMX-21) —
  i.e. without a private ``-L <name>`` / ``-S <path>`` socket. A ``TMUX_TMPDIR=…`` prefix
  does NOT count: inside a tmux pane ``$TMUX`` takes precedence over it, which is exactly
  how an agent's "private" ``TMUX_TMPDIR=/tmp/x tmux kill-server`` killed the live fleet
  (its own pane included) on 2026-10-06. ``kill-session`` is denied when it names chela's
  session (``$CHELA_TMUX_SESSION``, else ``chela``), a ``$id``, uses ``-a``, or names no
  target at all (inside a chela pane that is chela's session).

It never denies read-only ``gh pr view/diff/checks/list``, ``gh pr create``, or a push of a
``cmx-*`` feature branch. ``chela merge`` itself is unaffected: its own ``gh``/``git`` calls
run as subprocesses of chela's Python, never through a Claude ``Bash`` tool, so no hook
ever sees them.

The command is PARSED, not substring-matched: ``&&``/``||``/``;``/``|`` chains,
``$(…)``, ``bash -c "…"``/``eval``, env-var prefixes and wrappers (``env``, ``sudo``,
``timeout``…), ``cd x && …`` (tracked, so the repo is resolved where the merge would
actually run), ``git -C``, ``gh -R``/``GH_REPO``, and heredoc bodies (skipped — a PR body
that *mentions* ``gh pr merge`` is not a merge).

**Why a ``command`` hook and not the dashboard's ``http`` route.** The http route fails
OPEN by design (``chela/dashboard/app.py`` — a dead daemon must never wedge an agent), so a
gate that lived there would vanish the moment the dashboard went down. This script decides
LOCALLY from two files — the hook payload on stdin and ``$CHELA_DIR/mergegate.json`` (the
registry the dispatcher writes on every tick: each workflow's repo dir, GitHub slugs and
base branch) — in ~10 ms of interpreter start-up. It is **stdlib-only** and imports nothing
from chela, because it runs from the plugin cache (``${CLAUDE_PLUGIN_ROOT}/hooks/``), where
chela is not importable.

**The trade-off: it fails OPEN when it cannot decide.** An unparseable command (unbalanced
quotes), a missing/unreadable registry, or a merge-shaped command whose repo it cannot
resolve is ALLOWED — and the undecided case is appended to ``$CHELA_DIR/mergegate.log`` —
because a gate that wedges unrelated work in every session on a parse error is worse than
one that occasionally lets a human-scale mistake through to the backstop
(:func:`chela.dispatcher.tick`'s out-of-gate merge detection, which flags any merge the
gate never saw). It is a discipline enforcer for a cooperative-but-mistaken session, not a
sandbox against an adversarial one: a session that writes a script file and runs it, or
talks to the GitHub API with ``curl``, is out of its sight (``docs/SANDBOX_BOUNDARY.md``).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

REGISTRY_NAME = "mergegate.json"
LOG_NAME = "mergegate.log"
LOG_MAX_BYTES = 256 * 1024

# Mirrors chela.contract.FORBIDDEN_BASES (a test asserts they agree) — duplicated because
# this file must not import chela: it runs from the plugin cache.
FORBIDDEN_BASES = frozenset({"main", "master", "production", "prod", "release", "stable"})

# Commands that run their arguments as another command.
_WRAPPERS = frozenset({"command", "builtin", "exec", "nohup", "time", "sudo", "env",
                       "nice", "timeout", "stdbuf", "doas"})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_REDIRECTS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "&>", "&>>", "<&", ">|", "<>"})
_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SLUG_RE = re.compile(r"github\.com[:/]+([^/\s:]+)/([^/\s]+?)(?:\.git)?/?$")
_PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/\d+")

DENY_HINT = ("Use `chela merge <run>` (e.g. `chela merge cmx-12`) — it merges only when the "
             "judge is clean on the PR's current head, CI is green and the PR is mergeable. "
             "To merge past the judge, the operator approves `chela merge <run> --override "
             "--reason \"<why>\"`, which is audited.")


class Decision:
    """``deny`` + the reason shown to the agent; ``undecided`` names why it failed open."""

    __slots__ = ("deny", "reason", "undecided")

    def __init__(self, deny: bool = False, reason: str = "", undecided: str = ""):
        self.deny = deny
        self.reason = reason
        self.undecided = undecided

    def __repr__(self) -> str:                   # pragma: no cover — debugging aid
        return f"Decision(deny={self.deny}, reason={self.reason!r}, undecided={self.undecided!r})"


ALLOW = Decision()


# --- environment ---------------------------------------------------------------------

def chela_dir(env=None) -> Path:
    env = os.environ if env is None else env
    raw = env.get("CHELA_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".chela"


def registry_path(env=None) -> Path:
    return chela_dir(env) / REGISTRY_NAME


def load_registry(env=None) -> list[dict] | None:
    """The workflows chela dispatches, or None when the registry cannot be read."""
    try:
        data = json.loads(registry_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, list):
        return None
    return [r for r in repos if isinstance(r, dict)]


# --- git, read from the filesystem (no subprocess: this runs on every Bash call) ------

def _git_dir(start: Path) -> tuple[Path, Path] | None:
    """``(worktree_root, git_dir)`` for the repo containing ``start``, or None."""
    try:
        here = start.resolve()
    except OSError:
        return None
    for d in (here, *here.parents):
        dot = d / ".git"
        if dot.is_dir():
            return d, dot
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                gd = Path(text[len("gitdir:"):].strip())
                return d, (gd if gd.is_absolute() else (d / gd)).resolve()
            return None
    return None


def _common_dir(git_dir: Path) -> Path:
    """A linked worktree's git dir points at the main repo's through ``commondir``."""
    try:
        rel = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git_dir
    p = Path(rel)
    return (p if p.is_absolute() else git_dir / p).resolve()


def repo_root(start: str | Path) -> Path | None:
    """The MAIN checkout of the repo containing ``start`` — worktrees resolve to it."""
    found = _git_dir(Path(start))
    if found is None:
        return None
    root, git_dir = found
    common = _common_dir(git_dir)
    return common.parent if common.name == ".git" else root


def current_branch(start: str | Path) -> str | None:
    found = _git_dir(Path(start))
    if found is None:
        return None
    try:
        head = (found[1] / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None


def repo_slugs(start: str | Path) -> list[str]:
    """``owner/repo`` for every GitHub remote in the repo's config."""
    found = _git_dir(Path(start))
    if found is None:
        return []
    try:
        text = (_common_dir(found[1]) / "config").read_text(encoding="utf-8")
    except OSError:
        return []
    slugs: list[str] = []
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() != "url":
            continue
        m = _SLUG_RE.search(value.strip())
        if m:
            slug = f"{m.group(1)}/{m.group(2)}"
            if slug not in slugs:
                slugs.append(slug)
    return slugs


# --- the registry writer (chela side — called from the dispatcher's tick) --------------

def registry_entry(workflow_path: str | Path, bases) -> dict:
    wf = Path(workflow_path).expanduser()
    root = repo_root(wf.parent) or wf.parent
    return {
        "workflow": str(wf),
        "repo_dir": str(root),
        "slugs": repo_slugs(wf.parent),
        "bases": sorted({b.strip() for b in bases if isinstance(b, str) and b.strip()}),
    }


def register(workflow_path: str | Path, bases, env=None) -> bool:
    """Record one workflow in the registry. Writes only when the entry changed; never
    raises (a registry that cannot be written leaves the previous one in force)."""
    try:
        entry = registry_entry(workflow_path, bases)
        repos = load_registry(env) or []
        if entry in repos:
            return False
        repos = [r for r in repos if r.get("workflow") != entry["workflow"]] + [entry]
        path = registry_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"repos": repos}, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _match_slug(registry: list[dict], slug: str) -> dict | None:
    want = slug.strip().lower()
    for entry in registry:
        if want in [str(s).lower() for s in entry.get("slugs") or []]:
            return entry
    return None


def _match_dir(registry: list[dict], cwd: str) -> dict | None:
    root = repo_root(cwd)
    if root is None:
        return None
    for entry in registry:
        try:
            if Path(entry.get("repo_dir") or "").resolve() == root:
                return entry
        except OSError:
            continue
    return None


def protected(entry: dict) -> set[str]:
    return {b for b in entry.get("bases") or [] if isinstance(b, str)} | set(FORBIDDEN_BASES)


# --- parsing ---------------------------------------------------------------------------

def _strip_heredocs(command: str) -> str:
    out: list[str] = []
    pending: list[str] = []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending.extend(m.group(2) for m in _HEREDOC_RE.finditer(line))
    return "\n".join(out)


def _is_separator(token: str) -> bool:
    return bool(token) and all(c in "();|&" for c in token)


def segments(command: str) -> list[list[str]]:
    """The simple commands in ``command``, as token lists. Raises ValueError when the
    command cannot be tokenized (the caller fails open)."""
    text = _strip_heredocs(command).replace("\\\n", " ")
    text = text.replace("$(", " ( ").replace("`", " ; ").replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    out: list[list[str]] = []
    cur: list[str] = []
    skip_next = False
    for tok in lex:
        if skip_next:
            skip_next = False
            continue
        if _is_separator(tok):
            if cur:
                out.append(cur)
            cur = []
        elif tok in _REDIRECTS or (tok and all(c in "<>&|" for c in tok)):
            skip_next = True
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _unwrap(argv: list[str], env: dict) -> list[str]:
    """Drop ``VAR=x`` prefixes (recording them in ``env``), grouping braces and wrappers."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("{", "}", "!"):
            i += 1
        elif _ASSIGN_RE.match(tok):
            key, _, value = tok.partition("=")
            env[key] = value
            i += 1
        elif os.path.basename(tok) in _WRAPPERS:
            name = os.path.basename(tok)
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                flag = argv[i]
                i += 1
                if name in ("sudo", "doas") and flag in ("-u", "-g", "-C") and i < len(argv):
                    i += 1
                elif name == "nice" and flag == "-n" and i < len(argv):
                    i += 1
            if name == "timeout" and i < len(argv):
                i += 1                            # the duration
        else:
            break
    return argv[i:]


def _flag_value(args: list[str], names: tuple[str, ...]) -> str | None:
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                return args[i + 1]
            if n.startswith("--") and a.startswith(n + "="):
                return a[len(n) + 1:]
    return None


def _positionals(args: list[str], takes_value: frozenset[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out.extend(args[i + 1:])
            break
        if a.startswith("-") and a != "-":
            if a in takes_value:
                i += 1
            i += 1
            continue
        out.append(a)
        i += 1
    return out


class _Ctx:
    def __init__(self, cwd: str, env: dict, registry: list[dict] | None):
        self.cwd = cwd
        self.env = env
        self.registry = registry

    def resolve(self, *, slug: str | None = None, cwd: str | None = None) -> tuple[dict | None, str]:
        """``(entry, why_unknown)`` — entry None + empty reason means "not a chela repo"."""
        if self.registry is None:
            return None, f"no readable registry at {registry_path(self.env)}"
        if slug:
            return _match_slug(self.registry, slug), ""
        where = cwd or self.cwd
        if not where:
            return None, "no cwd to resolve the repo from"
        if repo_root(where) is None:
            return None, f"{where} is not inside a git repo"
        return _match_dir(self.registry, where), ""


def _deny(what: str, entry: dict) -> Decision:
    repo = (entry.get("slugs") or [entry.get("repo_dir")])[0]
    return Decision(True, f"⚖️ chela merge gate: {what} in {repo} — outside chela's gate. "
                          f"{DENY_HINT}")


def _gh(args: list[str], ctx: _Ctx) -> Decision:
    repo_flag = _flag_value(args, ("-R", "--repo")) or ctx.env.get("GH_REPO")
    pos = _positionals(args, frozenset({"-R", "--repo"}))
    if len(pos) >= 2 and pos[0] == "pr" and pos[1] == "merge":
        if "--help" in args or "-h" in args or "--disable-auto" in args:
            return ALLOW
        rest = _positionals(args[args.index("merge") + 1:], frozenset(
            {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
             "-A", "--author-email", "--match-head-commit"}))
        slug = repo_flag
        if rest:
            m = _PR_URL_RE.search(rest[0])
            if m:
                slug = f"{m.group(1)}/{m.group(2)}"
        entry, why = ctx.resolve(slug=slug)
        if entry is not None:
            return _deny(f"`gh pr merge {rest[0] if rest else ''}`".replace(" `", "`"), entry)
        return Decision(undecided=why) if why else ALLOW
    if pos and pos[0] == "api":
        return _gh_api(args[args.index("api") + 1:], ctx, repo_flag)
    return ALLOW


_API_VALUE_FLAGS = frozenset({"-X", "--method", "-H", "--header", "-f", "--raw-field",
                              "-F", "--field", "--input", "-q", "--jq", "-t", "--template",
                              "--hostname", "--cache", "-p", "--preview", "-R", "--repo"})


def _fields(args: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, a in enumerate(args):
        if a in ("-f", "-F", "--raw-field", "--field") and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            out[k] = v
        elif a.startswith(("--raw-field=", "--field=")):
            k, _, v = a.split("=", 1)[1].partition("=")
            out[k] = v
    return out


def _gh_api(args: list[str], ctx: _Ctx, repo_flag: str | None) -> Decision:
    pos = _positionals(args, _API_VALUE_FLAGS)
    if not pos:
        return ALLOW
    endpoint = pos[0].lstrip("/")
    fields = _fields(args)
    method = (_flag_value(args, ("-X", "--method")) or "").upper()
    if not method:
        method = "POST" if fields or "--input" in args else "GET"

    if endpoint == "graphql":
        query = " ".join(fields.values())
        if "mergePullRequest" in query or "enablePullRequestAutoMerge" in query:
            entry, why = ctx.resolve(slug=repo_flag)
            if entry is not None:
                return _deny("a GraphQL mergePullRequest mutation", entry)
            return Decision(undecided=why or "a GraphQL merge mutation names no known repo")
        return ALLOW

    m = re.match(r"^repos/([^/]+)/([^/]+)/(.*)$", endpoint)
    if not m:
        return ALLOW
    owner, name, rest = m.groups()
    slug = None if "{" in owner or "{" in name else f"{owner}/{name}"
    if slug is None:
        slug = repo_flag
    rest = rest.rstrip("/")

    def resolved() -> tuple[dict | None, str]:
        return ctx.resolve(slug=slug)

    if re.match(r"^pulls/\d+/merge$", rest) and method == "PUT":
        entry, why = resolved()
        if entry is not None:
            return _deny(f"`gh api -X PUT {endpoint}`", entry)
        return Decision(undecided=why) if why else ALLOW
    if rest == "merges" and method == "POST":
        entry, why = resolved()
        if entry is not None and fields.get("base") in protected(entry):
            return _deny(f"`gh api {endpoint}` into {fields.get('base')!r}", entry)
        return Decision(undecided=why) if why else ALLOW
    ref = re.match(r"^git/refs/heads/(.+)$", rest)
    if ref and method in ("PATCH", "DELETE", "PUT"):
        entry, why = resolved()
        if entry is not None and ref.group(1) in protected(entry):
            return _deny(f"`gh api -X {method} {endpoint}`", entry)
        return Decision(undecided=why) if why else ALLOW
    return ALLOW


_GIT_GLOBAL_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                               "--exec-path", "--config-env"})
_PUSH_VALUE = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})


def _branch_of(ref: str) -> str:
    ref = ref.lstrip("+")
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _git(args: list[str], ctx: _Ctx) -> Decision:
    cwd = ctx.cwd
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            cwd = os.path.join(cwd, os.path.expanduser(args[i + 1])) if cwd else args[i + 1]
        i += 2 if args[i] in _GIT_GLOBAL_VALUE else 1
    if i >= len(args) or args[i] != "push":
        return ALLOW
    push_args = args[i + 1:]
    if "--help" in push_args or "-h" in push_args:
        return ALLOW
    pos = _positionals(push_args, _PUSH_VALUE)
    everything = any(a in ("--all", "--mirror", "--branches") for a in push_args)
    deleting = any(a in ("-d", "--delete") for a in push_args)
    refspecs = pos[1:]
    if everything:
        entry, why = ctx.resolve(cwd=cwd)
        if entry is not None:
            return _deny("`git push --all/--mirror` (it pushes the protected branches too)",
                         entry)
        return Decision(undecided=why) if why else ALLOW

    targets: list[str] = []
    needs_head = not refspecs
    for spec in refspecs:
        if deleting:
            targets.append(_branch_of(spec))
        elif ":" in spec:
            targets.append(_branch_of(spec.rsplit(":", 1)[1]))
        elif spec.lstrip("+") in ("HEAD", "@"):
            needs_head = True
        else:
            targets.append(_branch_of(spec))
    if needs_head:
        branch = current_branch(cwd) if cwd else None
        if branch is None:
            entry, why = ctx.resolve(cwd=cwd)
            if entry is None:
                return Decision(undecided=why) if why else ALLOW
            return Decision(undecided="`git push` of HEAD, and the current branch could "
                                      "not be read")
        targets.append(branch)
    entry, why = ctx.resolve(cwd=cwd)
    if entry is None:
        return Decision(undecided=why) if why else ALLOW
    hit = sorted(t for t in targets if t in protected(entry))
    if hit:
        return _deny(f"`git push` to protected branch {hit[0]!r}", entry)
    return ALLOW


def _self_approval(argv: list[str]) -> Decision:
    """An override is the OPERATOR's call — a Claude session may not approve its own."""
    name = os.path.basename(argv[0])
    rest = argv[1:]
    if name == "chela" or (name in ("uv", "uvx") and "chela" in rest) or (
            name.startswith("python") and "chela.main" in rest):
        if "merge-approve" in rest:
            return Decision(True, "⚖️ chela merge gate: an override is approved by the "
                                  "OPERATOR, from the dashboard or a plain terminal — never "
                                  "by a Claude session. Ask the human to approve it.")
        if "share-requests" in rest and "approve" in rest:
            return Decision(True, SHARE_REQUEST_DENY)
    if name in ("curl", "wget", "http", "xh") and any("/override/" in a for a in rest):
        return Decision(True, "⚖️ chela merge gate: the dashboard's /override/ approval is "
                              "the OPERATOR's to press, not a Claude session's. Ask the "
                              "human to approve it.")
    if name in ("curl", "wget", "http", "xh") and any(
            "/api/share-requests/" in a and "approve" in a for a in rest):
        return Decision(True, SHARE_REQUEST_DENY)
    return ALLOW


# --- tmux: the live server (CMX-21) ------------------------------------------------------

_TMUX_VALUE_FLAGS = frozenset("cfLST")       # global flags that take a value
TMUX_DENY_HINT = ("Pin a PRIVATE server on every call — `tmux -L <name> …` or "
                  "`tmux -S <path> …`. `TMUX_TMPDIR=` alone does not isolate: inside a tmux "
                  "pane `$TMUX` overrides it, and a missing TMUX_TMPDIR dir falls back to the "
                  "default socket. That is how the live fleet was killed on 2026-10-06.")


def _tmux_globals(args: list[str]) -> tuple[str | None, str | None, int]:
    """``(-L name, -S path, index of the first command word)`` — clustered flags too."""
    name = path = None
    i = 0
    while i < len(args) and args[i].startswith("-") and len(args[i]) > 1:
        a = args[i]
        if a == "--":
            return name, path, i + 1
        for j, c in enumerate(a[1:], start=1):
            if c in _TMUX_VALUE_FLAGS:
                value = a[j + 1:]
                if not value and i + 1 < len(args):
                    i += 1
                    value = args[i]
                if c == "L":
                    name = value
                elif c == "S":
                    path = value
                break
        i += 1
    return name, path, i


def _private_socket(name: str | None, path: str | None) -> bool:
    if name is not None:
        return bool(name) and name != "default"
    if path is not None:
        p = Path(path)
        return bool(path) and not (p.name == "default" and p.parent.name.startswith("tmux-"))
    return False


def _tmux_commands(rest: list[str]) -> list[list[str]]:
    """``tmux a \\; b`` — the commands of one tmux invocation (``;`` arrives as its own
    token only when the shell lexer kept it; :func:`_decide_segments` covers the rest)."""
    out: list[list[str]] = [[]]
    for tok in rest:
        if tok in (";", "\\;"):
            out.append([])
        else:
            out[-1].append(tok)
    return [c for c in out if c]


def _is_cmd(word: str, full: str, min_len: int) -> bool:
    """tmux accepts any unambiguous prefix of a command name (``kill-ser``)."""
    return len(word) >= min_len and full.startswith(word)


def _tmux_kill(cmd: list[str], env: dict) -> str | None:
    """What ``cmd`` would kill on the live server, or None."""
    word, args = cmd[0], cmd[1:]
    if _is_cmd(word, "kill-server", 8):
        return "`tmux kill-server`"
    if not _is_cmd(word, "kill-session", 8):
        return None
    live = {"chela", env.get("CHELA_TMUX_SESSION") or "chela"}
    if "-a" in args:
        return "`tmux kill-session -a` (it kills every OTHER session, chela's included)"
    target = _flag_value(args, ("-t",))
    if target is None:
        attached = [a for a in args if a.startswith("-t") and len(a) > 2]
        target = attached[0][2:] if attached else None
    if target is None:
        return "`tmux kill-session` with no -t (inside a chela pane that is chela's session)"
    session = target.lstrip("=").split(":", 1)[0]
    if session in live or session.startswith("$"):
        return f"`tmux kill-session -t {target}`"
    return None


def _tmux(args: list[str], ctx: _Ctx) -> Decision:
    name, path, i = _tmux_globals(args)
    if _private_socket(name, path):
        return ALLOW
    for cmd in _tmux_commands(args[i:]):
        what = _tmux_kill(cmd, ctx.env)
        if what:
            return Decision(True, f"🧯 chela: {what} on the LIVE tmux server — it would take "
                                  f"down every agent pane, this one included. "
                                  f"{TMUX_DENY_HINT}")
    return ALLOW


SHARE_REQUEST_DENY = ("🙋 chela: a sandboxed guest's access request is approved by the "
                      "OPERATOR, from the dashboard or a plain terminal — never by a Claude "
                      "session. Ask the human to decide it.")


def _decide_segments(command: str, ctx: _Ctx, depth: int = 0) -> Decision:
    if depth > 4:
        return Decision(undecided="nested shell commands too deep to follow")
    try:
        segs = segments(command)
    except ValueError as exc:
        merge_shaped = any(k in command for k in ("merge", "push", "override"))
        return Decision(undecided=f"unparseable command ({exc})") if merge_shaped else ALLOW
    undecided = ""
    tmux_globals: list[str] | None = None    # the last tmux call's globals, for `\;` chains
    for raw in segs:
        env = dict(ctx.env)
        argv = _unwrap(raw, env)
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if name == "tmux":
            tmux_globals = argv[1:1 + _tmux_globals(argv[1:])[2]]
        elif tmux_globals is not None and name.startswith("kill-"):
            # `tmux new -d \; kill-server`: the lexer split the escaped `;` into its own
            # segment, so this word is still a command of the tmux call before it.
            argv, name = ["tmux", *tmux_globals, *argv], "tmux"
        else:
            tmux_globals = None
        sub = _Ctx(ctx.cwd, env, ctx.registry)
        if name in ("cd", "pushd"):
            target = argv[1] if len(argv) > 1 and argv[1] != "-" else "~"
            target = os.path.expanduser(target)
            ctx.cwd = os.path.normpath(os.path.join(ctx.cwd or "/", target))
            continue
        if name in _SHELLS:
            script = None
            for j, a in enumerate(argv[1:], start=1):
                if a.startswith("-") and not a.startswith("--") and "c" in a:
                    script = argv[j + 1] if j + 1 < len(argv) else None
                    break
            d = _decide_segments(script, sub, depth + 1) if script is not None else ALLOW
        elif name == "eval":
            d = _decide_segments(" ".join(argv[1:]), sub, depth + 1)
        elif name == "gh":
            d = _gh(argv[1:], sub)
        elif name == "git":
            d = _git(argv[1:], sub)
        elif name == "tmux":
            d = _tmux(argv[1:], sub)
        else:
            d = _self_approval(argv)
        if d.deny:
            return d
        undecided = undecided or d.undecided
    return Decision(undecided=undecided)


def decide(payload: dict, env=None, registry: list[dict] | None | bool = False) -> Decision:
    """The verdict for one ``PreToolUse`` payload. Pure apart from reading the registry and
    the repo's ``.git`` files. ``registry=False`` means "load it from ``$CHELA_DIR``"."""
    env = dict(os.environ if env is None else env)
    if payload.get("tool_name") != "Bash":
        return ALLOW
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return ALLOW
    reg = load_registry(env) if registry is False else registry
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    return _decide_segments(command, _Ctx(cwd, env, reg))


def response(decision: Decision) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": decision.reason,
    }}


def _log(env, record: dict) -> None:
    try:
        path = chela_dir(env) / LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(stdin=None, stdout=None, env=None) -> int:
    """The hook entry point: payload on stdin, a deny on stdout (or nothing), exit 0."""
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        decision = decide(payload, env)
    except Exception as exc:                     # noqa: BLE001 — never wedge a session
        _log(env, {"ts": time.time(), "decision": "allow", "undecided": f"error: {exc!r}"})
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")[:500]
    if decision.deny:
        stdout.write(json.dumps(response(decision)) + "\n")
        _log(env, {"ts": time.time(), "decision": "deny", "reason": decision.reason,
                   "command": command, "cwd": payload.get("cwd"),
                   "session_id": payload.get("session_id")})
    elif decision.undecided:
        _log(env, {"ts": time.time(), "decision": "allow", "undecided": decision.undecided,
                   "command": command, "cwd": payload.get("cwd"),
                   "session_id": payload.get("session_id")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
