"""📐🔗 CMX-432 — a Linear tracker: ``tracker: kind: linear``.

Sibling of :mod:`chela.sources.markdown` and :mod:`chela.sources.gh_issues`, duck-typed the
same way (``__init__(wf)`` + ``list_open_tasks()`` + ``read_failed``), plus the hooks a
network tracker that chela itself writes to needs:

* :meth:`LinearSource.fetch_by_ids` — G1's ID-refresh contract
  (``docs/SYMPHONY_CONFORMANCE_2026-09-13.md``): the current snapshot of the named issues,
  ``None`` on ANY failed read, never ``[]``. An ARCHIVED issue is terminal (done), never
  absent.
* :meth:`LinearSource.claimable` — what :func:`chela.dispatcher._claim_order` may claim:
  only the configured ready state(s), ordered priority-then-manual-order, with the
  ``blockedBy`` closures :func:`chela.dispatcher._ready` needs.
* :meth:`LinearSource.close_tasks` — chela marks a merged task Done (the fallback for the
  GitHub integration, which may not fire for merges into ``dev``). It does NOT archive it.
* :meth:`LinearSource.transition` / :meth:`LinearSource.cancel_task` — 🗂️📐 CMX-23: chela
  drives the issue through the team's workflow (claim → In Progress, PR open → In Review,
  ``chela close`` → Canceled, or Duplicate + a ``duplicate`` relation when the close reason
  names the superseding issue). Never raises; never moves a finished issue.
* :meth:`LinearSource.workflow_states` — the team's states in Linear's board order, for the
  dashboard's Work board (Canceled-type columns flagged ``hidden``, as Linear hides them).
* :meth:`LinearSource.archive_sweep` — the one place chela archives (CMX-8): it keeps the
  ``keep_done`` most recently finished issues visible and archives only the finished issues
  older than those, whoever closed them, then publishes the team's non-archived issue count
  for ``chela doctor`` (the free plan caps a workspace at 250).

Config (workflow front matter, ``tracker:`` block)::

    tracker:
      kind: linear
      team: CMX                  # the team KEY (REQUIRED) — also the identifier prefix
      ready_states: [Todo]       # the state NAMES a task may be CLAIMED from (default Todo)
      done_state: Done           # optional — the state chela sets on merge; default: the
                                 # team's first state of type `completed`
      keep_done: 100             # optional — how many of the most recently finished
                                 # (Done + Canceled) issues stay unarchived; 0 = archive
                                 # every finished issue. Default 100.
      states:                    # optional (CMX-23) — the state NAMES chela moves an issue
        in_progress: In Progress #   to on claim / PR open / close. These are the defaults;
        in_review: In Review     #   a name the team lacks falls back to the state TYPE
        canceled: Canceled       #   (first/last `started`, first `canceled`), else the
        duplicate: Duplicate     #   write is skipped and logged.

🔐 The API key is read from ``$CHELA_DIR/chela.env`` as exactly ``LINEAR_API_KEY`` — through
:func:`chela.config.parse_env_file`, at the moment a source is built, never from WORKFLOW.md,
never from the repo. A ``tracker.api_key`` (or anything key/token-shaped) in WORKFLOW.md is
REFUSED, not used. The value is never logged, never put in an exception message, and never
reaches a child process (:func:`chela.envutil.child_env` strips ``LINEAR_API_KEY``, CMX-425).

The OPEN set (:meth:`list_open_tasks`) is every issue whose state TYPE is not
``completed``/``canceled`` — backlog, unstarted AND started. Linear's GitHub integration moves
an issue to In Progress / In Review by itself when the PR opens; an open set of just ``Todo``
would make every such issue read as ABSENT, and the dispatcher reads absence as "done".
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from chela.sources import Task, apply_risk, highest_risk
from chela.workflow import WorkflowDef

log = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
API_KEY_ENV = "LINEAR_API_KEY"
DEFAULT_READY_STATES = ("Todo",)
# Linear's state TYPES. Terminal ones leave the open set; everything else is open.
TERMINAL_STATE_TYPES = ("completed", "canceled")
# 🗂️📐 CMX-23 — the order Linear's board shows its columns in: by state TYPE, then the
# state's own `position` inside its type. Canceled-type states (Canceled, Duplicate) are
# collapsed off the board by default in Linear, so the Work board hides them too.
STATE_TYPE_ORDER = ("triage", "backlog", "unstarted", "started", "completed", "canceled")
HIDDEN_STATE_TYPES = ("canceled",)
# The edges chela writes (CMX-23), and the default state NAME each one moves an issue to.
# `done` is not here: merge → Done is `close_tasks` (and the `done_state` key), unchanged.
DEFAULT_TRANSITION_STATES = {
    "in_progress": "In Progress",
    "in_review": "In Review",
    "canceled": "Canceled",
    "duplicate": "Duplicate",
}
TEAM_STATES_TTL_SECONDS = 10 * 60        # the dashboard polls; a team's states rarely change

# 🆓 The free plan caps a workspace at 250 NON-archived issues (Done/Canceled count,
# archived ones do not). Doctor WARNs above this, with ~50 issues of headroom left.
ISSUE_COUNT_WARN_AT = 200
ISSUE_CAP = 250

PAGE_SIZE = 100
MAX_PAGES = 10                       # 1000 issues — four times the free plan's whole cap
ARCHIVE_SWEEP_INTERVAL_SECONDS = 15 * 60
ARCHIVES_PER_SWEEP = 50              # a backlog clears over a few sweeps, never in one burst
# CMX-8: the N most recently finished issues stay on the board; only older ones are archived.
# Cap arithmetic: 250 − open − keep_done is the headroom left for new issues.
DEFAULT_KEEP_DONE = 100
HTTP_TIMEOUT_SECONDS = 20
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 15 * 60

# The WORKFLOW.md keys that would put a credential in a (possibly public) repo file.
_CREDENTIAL_KEY_RE = re.compile(r"(?:api_?key|token|secret|password)", re.IGNORECASE)


class LinearError(Exception):
    """A failed Linear call. ``kind`` is one of network/auth/rate_limited/http/graphql/
    malformed/backing_off. The message carries the HTTP status or the GraphQL error text —
    never a header, never the request, so never the key."""

    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.retry_after = retry_after


Transport = Callable[[str, dict], dict]

# Per-team process state that must outlive one tick's source object (a fresh source is
# built every tick): the 429 back-off window, the last sweep, the one-shot error log.
_backoff: dict[str, tuple[float, int]] = {}      # team → (retry-at monotonic, strikes)
_last_sweep: dict[str, float] = {}               # team → monotonic of the last sweep
_reported: set[tuple[str, str]] = set()
_team_states: dict[str, tuple[float, list[dict]]] = {}   # team → (monotonic, ordered states)


def _report_once(key: tuple[str, str], message: str) -> None:
    if key not in _reported:
        _reported.add(key)
        log.error("%s", message)


def load_api_key() -> str | None:
    """``LINEAR_API_KEY`` from the chela env file — the loader, not ``os.environ``. None
    when the file or the line is missing (or the file is disabled: ``CHELA_ENV_FILE=""``)."""
    from chela import config

    path = config.env_file_path()
    if path is None:
        return None
    value = config.parse_env_file(path).get(API_KEY_ENV, "").strip()
    return value or None


def _http_transport(api_key: str, urlopen=urllib.request.urlopen) -> Transport:
    """A transport that POSTs one GraphQL document to Linear and returns its ``data``.
    Every failure is a :class:`LinearError` — nothing else escapes."""

    def call(query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(
            LINEAR_API_URL, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": api_key},
        )
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LinearError("auth", f"HTTP {e.code}") from None
            if e.code == 429:
                raise LinearError("rate_limited", "HTTP 429",
                                  _retry_after(e.headers)) from None
            # Linear reports RATELIMITED as a GraphQL error on an HTTP 400.
            try:
                payload = json.loads(e.read() or b"{}")
            except (ValueError, OSError):
                payload = {}
            _raise_graphql_errors(payload, fallback=f"HTTP {e.code}")
            raise LinearError("http", f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise LinearError("network", type(e).__name__) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise LinearError("malformed", "response is not JSON") from None
        if not isinstance(payload, dict):
            raise LinearError("malformed", "response is not an object")
        _raise_graphql_errors(payload)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearError("malformed", "response has no data")
        return data

    return call


def _retry_after(headers) -> float | None:
    try:
        return float(headers.get("Retry-After")) if headers is not None else None
    except (TypeError, ValueError):
        return None


def _raise_graphql_errors(payload: dict, fallback: str = "") -> None:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not errors:
        return
    first = errors[0] if isinstance(errors, list) and errors else {}
    code = ""
    message = ""
    if isinstance(first, dict):
        ext = first.get("extensions") or {}
        code = str(ext.get("code") or "") if isinstance(ext, dict) else ""
        message = str(first.get("message") or "")[:200]
    if code == "RATELIMITED":
        raise LinearError("rate_limited", message or "RATELIMITED")
    if code in ("AUTHENTICATION_ERROR", "FORBIDDEN"):
        raise LinearError("auth", message or code)
    raise LinearError("graphql", message or code or fallback or "error")


def make_transport(api_key: str) -> Transport:
    """The production transport. A seam: the suite replaces it — no test talks to Linear."""
    return _http_transport(api_key)


_ISSUE_FIELDS = """
  id identifier number title description priority sortOrder branchName url archivedAt
  state { name type }
  labels(first: 50) { nodes { name } }
  inverseRelations(first: 50, includeArchived: true) {
    nodes { type issue { identifier archivedAt state { type } } }
  }
"""

OPEN_ISSUES_QUERY = """
query OpenIssues($team: String!, $after: String) {
  issues(first: %d, after: $after, includeArchived: false,
         filter: { team: { key: { eq: $team } },
                   state: { type: { nin: ["completed", "canceled"] } } }) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}
""" % (PAGE_SIZE, _ISSUE_FIELDS)

ISSUES_BY_NUMBER_QUERY = """
query IssuesByNumber($team: String!, $numbers: [Float!], $after: String) {
  issues(first: %d, after: $after, includeArchived: true,
         filter: { team: { key: { eq: $team } }, number: { in: $numbers } }) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}
""" % (PAGE_SIZE, _ISSUE_FIELDS)

TEAM_STATES_QUERY = """
query TeamStates($team: String!) {
  teams(filter: { key: { eq: $team } }) {
    nodes { id states { nodes { id name type position } } }
  }
}
"""

SWEEP_QUERY = """
query UnarchivedIssues($team: String!, $after: String) {
  issues(first: %d, after: $after, includeArchived: false,
         filter: { team: { key: { eq: $team } } }) {
    nodes { id identifier number archivedAt completedAt canceledAt state { type } }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE

UPDATE_STATE_MUTATION = """
mutation SetState($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}
"""

ARCHIVE_MUTATION = """
mutation Archive($id: String!) {
  issueArchive(id: $id) { success }
}
"""

# CMX-6: the dashboard's "New task". ⛔ The input never carries `parentId` — chela never
# creates a sub-issue (the free plan's cap counts them, and a sub-issue is not a task).
CREATE_ISSUE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier title url } }
}
"""

# `blockedBy` is a `blocks` relation stored on the BLOCKER (see _blockers): issueId is the
# blocker, relatedIssueId the issue it blocks.
CREATE_RELATION_MUTATION = """
mutation BlockRelation($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) { success }
}
"""

PRIORITIES = (0, 1, 2, 3, 4)          # No priority, Urgent, High, Medium, Low

# 🗂️📐 CMX-23: "<this issue> is a duplicate of <that one>" — Linear's own `duplicate`
# relation: issueId is the duplicate, relatedIssueId the issue that supersedes it.
# A close reason names the superseding issue only next to one of these words, so a reason
# that merely mentions another issue ("flaky, see CMX-30") is a plain Canceled.
_SUPERSEDED_RE = re.compile(r"supersed|duplicat|\bdup(?:e)?\b|replaced|subsumed|folded",
                            re.IGNORECASE)


def counts_path() -> Path:
    """Where the daemon's archive sweep publishes each team's non-archived issue count —
    read by ``chela doctor`` (``tracker.linear_issue_cap``) without a network call."""
    from chela import config

    return config.CHELA_DIR / "linear-issue-counts.json"


def read_published_counts() -> dict[str, dict]:
    try:
        data = json.loads(counts_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _publish_count(team: str, count: int) -> None:
    counts = read_published_counts()
    counts[team] = {"count": int(count), "at": time.time()}
    try:
        path = counts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(counts, indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError as e:
        log.warning("linear: could not publish the issue count for %s: %s", team, e)


def over_issue_warning(count: int) -> bool:
    """True when ``count`` non-archived issues is close enough to the free plan's cap
    that someone should look — the threshold doctor and the sweep share."""
    return count > ISSUE_COUNT_WARN_AT


def _keep_done(value: object) -> int:
    """``tracker.keep_done`` as a count ≥ 0. Anything else (unset, negative, not a whole
    number) is the default, logged once when it was set — a typo never turns into
    "archive everything"."""
    if value is None:
        return DEFAULT_KEEP_DONE
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    _report_once(("keep_done", repr(value)),
                 f"linear: tracker.keep_done={value!r} is not a whole number ≥ 0 — using "
                 f"{DEFAULT_KEEP_DONE}")
    return DEFAULT_KEEP_DONE


def _finished_at(node: dict) -> datetime:
    """When a finished issue finished: ``completedAt`` (Done) or ``canceledAt`` (Canceled).
    One without a readable time sorts as the OLDEST — it is archived first rather than
    pinning a slot of the newest ``keep_done`` forever."""
    state = node.get("state") if isinstance(node.get("state"), dict) else {}
    first, second = ("canceledAt", "completedAt") if state.get("type") == "canceled" else (
        "completedAt", "canceledAt")
    for key in (first, second):
        raw = node.get(key)
        if isinstance(raw, str) and raw:
            try:
                at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48].strip("-")


def _branch_for(identifier: str, title: str, suggested: object) -> str:
    """Linear's own suggested ``branchName`` (identifier + title slug) when it is a sane
    branch name that still CARRIES the identifier — that is what Linear's GitHub
    integration links on, and what makes it never equal an old exact ``cmx-N`` branch.
    Otherwise the same shape, built here."""
    ident = identifier.lower()
    if isinstance(suggested, str):
        b = suggested.strip().lower()
        if (b and re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", b) and ".." not in b
                and re.search(rf"(?:^|/){re.escape(ident)}-", b)):
            return b
    slug = _slug(title)
    return f"{ident}-{slug}" if slug else f"{ident}-task"


class LinearSource:
    """Pull open work from a Linear team. See the module docstring for the contract."""

    def __init__(self, wf: WorkflowDef, *, transport: Transport | None = None):
        self.workflow_path = wf.path
        self.team = str(wf.get("tracker", "team", default="") or "").strip()
        ready = wf.get("tracker", "ready_states", default=None)
        if isinstance(ready, str):
            ready = [ready]
        self.ready_states: tuple[str, ...] = tuple(
            str(s).strip() for s in (ready or DEFAULT_READY_STATES) if str(s).strip()
        ) or DEFAULT_READY_STATES
        done = wf.get("tracker", "done_state", default=None)
        self.done_state: str | None = str(done).strip() if done else None
        self.keep_done = _keep_done(wf.get("tracker", "keep_done", default=None))
        named = wf.get("tracker", "states", default=None)
        named = named if isinstance(named, dict) else {}
        self.transition_states: dict[str, str] = {
            edge: (str(named.get(edge)).strip() if named.get(edge) else default)
            for edge, default in DEFAULT_TRANSITION_STATES.items()
        }
        self.config_error: str | None = None
        # Same meaning as markdown/gh_issues: True when THIS tick's list_open_tasks() did
        # NOT read the tracker, so [] must not be taken as "nothing is open".
        self.read_failed = False
        self._cache: dict[tuple, dict] = {}       # one tick's reads (a source lives a tick)
        self._snapshot: list[dict] | None = None  # list_open_tasks' raw nodes
        self._transport: Transport | None = transport

        tracker = wf.get("tracker", default={}) or {}
        leaked = sorted(k for k in tracker if isinstance(k, str) and _CREDENTIAL_KEY_RE.search(k))
        if leaked:
            # Named, never echoed: the value may well be the real key.
            self.config_error = (
                f"{wf.path}: tracker.{leaked[0]} is set in WORKFLOW.md. The Linear API key "
                f"is read ONLY from chela.env as {API_KEY_ENV} — never from a workflow file, "
                "which lives in a repo. Remove it from WORKFLOW.md (and rotate the key if "
                "this file was ever committed). Claiming NO work until then."
            )
        elif not self.team:
            self.config_error = (
                f"{wf.path}: tracker.team is not set. A linear tracker needs the team KEY "
                "(e.g. `team: CMX`). Claiming NO work until then."
            )
        elif self._transport is None:
            key = load_api_key()
            if not key:
                self.config_error = (
                    f"{wf.path}: no {API_KEY_ENV} in chela.env. Add the line "
                    f"`{API_KEY_ENV}=<key>` to $CHELA_DIR/chela.env (a Linear personal API "
                    "key for this team). Only this workflow stops; claiming NO work until "
                    "then."
                )
            else:
                self._transport = make_transport(key)

    # --- transport, with the per-team back-off and the per-tick cache ---------------------

    def _call(self, query: str, variables: dict, *, cache: bool = True) -> dict:
        if self.config_error or self._transport is None:
            raise LinearError("config", "tracker is not configured")
        key = (query, json.dumps(variables, sort_keys=True))
        if cache and key in self._cache:
            return self._cache[key]
        retry_at, strikes = _backoff.get(self.team, (0.0, 0))
        if time.monotonic() < retry_at:
            raise LinearError("backing_off", f"rate limited; retrying in "
                                             f"{int(retry_at - time.monotonic())}s")
        try:
            data = self._transport(query, variables)
        except LinearError as e:
            if e.kind == "rate_limited":
                wait = e.retry_after or min(BACKOFF_MAX_SECONDS,
                                            BACKOFF_BASE_SECONDS * (2 ** strikes))
                _backoff[self.team] = (time.monotonic() + wait, strikes + 1)
                log.warning("linear: rate limited for team %s — backing off %ds",
                            self.team, int(wait))
            raise
        except Exception as e:                 # a stub, or anything the transport missed
            raise LinearError("network", type(e).__name__) from None
        _backoff.pop(self.team, None)
        if not isinstance(data, dict):
            raise LinearError("malformed", "data is not an object")
        if cache:
            self._cache[key] = data
        return data

    def _paged(self, query: str, variables: dict, *, cache: bool = True) -> list[dict]:
        nodes: list[dict] = []
        after = None
        for _ in range(MAX_PAGES):
            data = self._call(query, {**variables, "after": after}, cache=cache)
            conn = data.get("issues")
            if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
                raise LinearError("malformed", "issues connection missing")
            nodes.extend(conn["nodes"])
            page = conn.get("pageInfo") or {}
            if not (isinstance(page, dict) and page.get("hasNextPage") and page.get("endCursor")):
                return nodes
            after = page["endCursor"]
        log.warning("linear: team %s has more than %d issues in one query — read the "
                    "first %d", self.team, PAGE_SIZE * MAX_PAGES, PAGE_SIZE * MAX_PAGES)
        return nodes

    # --- reading -------------------------------------------------------------------------

    def _open_nodes(self) -> list[dict] | None:
        """The raw open-set nodes for this tick, or None when the read failed."""
        if self._snapshot is not None:
            return self._snapshot
        if self.config_error:
            _report_once((str(self.workflow_path), "config"), self.config_error)
            return None
        try:
            self._snapshot = self._paged(OPEN_ISSUES_QUERY, {"team": self.team})
        except LinearError as e:
            log.warning("linear: could not list open issues for team %s: %s", self.team, e)
            return None
        return self._snapshot

    def list_open_tasks(self) -> list[Task]:
        """Every NON-terminal issue in the team (backlog, unstarted, started) — the set
        reconciliation reads "still open" from. Claiming narrows it (:meth:`claimable`)."""
        nodes = self._open_nodes()
        if nodes is None:
            self.read_failed = True
            return []
        self.read_failed = False
        tasks = []
        for node in nodes:
            task = self._task(node)
            if task is None:
                # SPEC 11.1: a state-list read MAY drop a malformed record — it was never
                # safe to dispatch — and SHOULD say so.
                log.warning("linear: skipping a malformed issue record in team %s", self.team)
                continue
            tasks.append(task)
        return self._sorted(tasks, nodes)

    def fetch_by_ids(self, ids) -> list[Task] | None:
        """G1: the CURRENT snapshot of each requested task. ``None`` when the read failed —
        network, auth, rate limit, a malformed response or a malformed requested record —
        never ``[]``, because absence is meaningful to the caller. An archived issue comes
        back with ``terminal_state`` set (done), never as missing. An id that is not one
        of this team's identifiers is not this tracker's, and is simply not returned."""
        if self.config_error:
            _report_once((str(self.workflow_path), "config"), self.config_error)
            return None
        nodes = self._nodes_by_id(ids)
        if nodes is None:
            return None
        tasks = []
        for node in nodes:
            task = self._task(node)
            if task is None:
                log.warning("linear: a requested issue in team %s came back malformed — "
                            "failing the whole refresh", self.team)
                return None
            tasks.append(task)
        return tasks

    def _nodes_by_id(self, ids, *, cache: bool = True) -> list[dict] | None:
        """The raw nodes for ``ids`` (archived ones included), or None on a failed read."""
        numbers = sorted({n for n in (self._number_of(i) for i in ids) if n is not None})
        if not numbers:
            return []
        try:
            return self._paged(ISSUES_BY_NUMBER_QUERY, {"team": self.team, "numbers": numbers},
                               cache=cache)
        except LinearError as e:
            log.warning("linear: could not refresh %d issue(s) for team %s: %s",
                        len(numbers), self.team, e)
            return None

    def claimable(self, tasks: list[Task], retry_ids=frozenset()) -> tuple[list[Task], set[str]]:
        """``(candidates, closed_ids)`` for :func:`chela.dispatcher._ready`: the tasks in a
        configured READY state, in claim order, and the ids of every blocker known DONE.

        🗂️📐 CMX-23: ``retry_ids`` are tasks whose last run DIED (``failed``) — chela itself
        moved their issue to In Progress / In Review when it claimed them, so demanding the
        ready state again would strand the retry forever. Such a task is a candidate from
        any ``started``-type state too. Not from Backlog: a human parking it there is a
        decision, not a leftover. The attempt cap is the dispatcher's (``MAX_ATTEMPTS``).

        A blocker counts as done only when its state type is ``completed`` — a canceled,
        open, or unreadable blocker (the relation is there but the issue is not) holds the
        task, exactly as markdown's unstruck or unresolved ``depends:`` does."""
        nodes = self._snapshot or []
        by_ident = {n.get("identifier"): n for n in nodes if isinstance(n, dict)}
        ready_names = {s.lower() for s in self.ready_states}
        candidates = []
        closed: set[str] = set()
        for t in tasks:
            node = by_ident.get(t.id)
            if node is None:
                continue
            for blocker in _blockers(node):
                if blocker["done"]:
                    closed.add(blocker["identifier"])
            state = node.get("state") if isinstance(node.get("state"), dict) else {}
            if str(state.get("name") or "").lower() in ready_names:
                candidates.append(t)
            elif t.id in retry_ids and state.get("type") == "started":
                candidates.append(t)
        return candidates, closed

    def workflow_states(self) -> list[dict] | None:
        """🗂️📐 CMX-23 — the team's workflow states as the Work board's columns, in Linear's
        board order (:data:`STATE_TYPE_ORDER`, then ``position``): ``[{"name", "type",
        "hidden"}]``. ``hidden`` marks the Canceled-type states Linear collapses. None when
        the team cannot be read. Cached per team for :data:`TEAM_STATES_TTL_SECONDS` — the
        dashboard asks on every poll, and a team's states almost never change."""
        cached = _team_states.get(self.team)
        if cached and time.monotonic() - cached[0] < TEAM_STATES_TTL_SECONDS:
            return [dict(s) for s in cached[1]]
        if self.config_error:
            return None
        team = self._team()
        if team is None:
            return None
        rank = {t: i for i, t in enumerate(STATE_TYPE_ORDER)}

        def key(s: dict):
            try:
                pos = float(s.get("position") or 0)
            except (TypeError, ValueError):
                pos = 0.0
            return (rank.get(s.get("type"), len(rank)), pos)

        states = [
            {"name": str(s["name"]), "type": str(s.get("type") or ""),
             "hidden": s.get("type") in HIDDEN_STATE_TYPES}
            for s in sorted(team[1], key=key) if isinstance(s.get("name"), str) and s["name"]
        ]
        _team_states[self.team] = (time.monotonic(), states)
        return [dict(s) for s in states]

    # --- writing -------------------------------------------------------------------------

    def close_tasks(self, task_ids, *, at: Path | None = None) -> dict[str, str]:
        """Mark each merged task Done (unless it already is). It does NOT archive it
        (CMX-8): the sweep applies the ``keep_done`` rule, and runs once right after any
        close here so the free-plan cap cannot creep. Same outcomes as markdown's strike:
        ``struck`` / ``already`` / ``missing``; ``failed`` when Linear refused. Idempotent,
        and it never raises: a write that fails is logged and retried next tick (the task is
        still open, so the dispatcher still lists it as pending)."""
        del at                                   # a file-tracker concept; nothing to redirect
        results = {tid: "missing" for tid in task_ids}
        if not results:
            return results
        if self.config_error:
            return {tid: "failed" for tid in results}
        found = self._nodes_by_id(list(results))
        if found is None:
            return {tid: "failed" for tid in results}
        nodes = {n.get("identifier"): n for n in found if isinstance(n, dict) and n.get("id")}
        done_state_id = None
        for tid in results:
            node = nodes.get(tid)
            if node is None:
                continue
            state = node.get("state") or {}
            if state.get("type") in TERMINAL_STATE_TYPES or node.get("archivedAt"):
                results[tid] = "already"
            else:
                if done_state_id is None:
                    done_state_id = self._done_state_id()
                if done_state_id is None:
                    results[tid] = "failed"
                    continue
                try:
                    data = self._call(UPDATE_STATE_MUTATION,
                                      {"id": node["id"], "stateId": done_state_id}, cache=False)
                    ok = bool((data.get("issueUpdate") or {}).get("success"))
                except LinearError as e:
                    log.warning("linear: could not mark %s done: %s", tid, e)
                    ok = False
                if not ok:
                    results[tid] = "failed"
                    continue
                results[tid] = "struck"
        if "struck" in results.values():
            # A finished issue was just added: if that put the team over keep_done, the
            # oldest goes now, not up to 15 minutes later.
            self.archive_sweep(force=True)
        return results

    def transition(self, task_id: str, edge: str, *, duplicate_of: str | None = None) -> str:
        """🗂️📐 CMX-23 — move one issue along the edge chela just crossed: ``in_progress``
        (claimed), ``in_review`` (its PR is open), ``canceled`` or ``duplicate`` (``chela
        close``). Returns ``set`` (written), ``already`` (it is in that state), ``skipped``
        (the issue is finished — never resurrected — or the team has no such state),
        ``missing`` (no such issue) or ``failed`` (a read or the write failed; the caller
        may try again). Never raises: a tracker write must never block a claim, a close or
        a merge. ``duplicate_of`` (an identifier of this team) also adds Linear's
        ``duplicate`` relation; a relation that fails after the state landed is logged."""
        if edge not in DEFAULT_TRANSITION_STATES:
            log.warning("linear: unknown transition %r for %s", edge, task_id)
            return "skipped"
        if self.config_error:
            return "failed"
        try:
            # Uncached: the state this tick's open read saw may be one chela just changed.
            found = self._nodes_by_id([task_id], cache=False)
            if found is None:
                return "failed"
            node = next((n for n in found if isinstance(n, dict) and n.get("id")
                         and str(n.get("identifier") or "").upper() == str(task_id).upper()),
                        None)
            if node is None:
                return "missing"
            state = node.get("state") if isinstance(node.get("state"), dict) else {}
            if state.get("type") in TERMINAL_STATE_TYPES or node.get("archivedAt"):
                return "skipped"
            target = self._transition_state(edge)
            if target is None:
                return "skipped"
            if str(state.get("name") or "").lower() == str(target.get("name") or "").lower():
                return "already"
            data = self._call(UPDATE_STATE_MUTATION, {"id": node["id"], "stateId": target["id"]},
                              cache=False)
            if not (data.get("issueUpdate") or {}).get("success"):
                log.warning("linear: moving %s to %s was refused", task_id, target.get("name"))
                return "failed"
        except LinearError as e:
            log.warning("linear: could not move %s to %s: %s", task_id, edge, e)
            return "failed"
        except Exception:                       # never a crash in the caller's path
            log.exception("linear: moving %s to %s failed", task_id, edge)
            return "failed"
        log.info("linear: %s → %s", task_id, target.get("name"))
        if edge == "duplicate" and duplicate_of:
            self._relate_duplicate(task_id, node["id"], duplicate_of)
        return "set"

    def cancel_task(self, task_id: str, reason: str) -> str:
        """``chela close <run> --reason`` → Canceled, or Duplicate (+ the relation) when the
        reason names the issue that supersedes this one. Same outcomes as
        :meth:`transition`."""
        superseding = self.superseding_issue(reason, task_id)
        if superseding:
            return self.transition(task_id, "duplicate", duplicate_of=superseding)
        return self.transition(task_id, "canceled")

    def superseding_issue(self, reason: str, own_id: str) -> str | None:
        """The identifier of this team's issue a close ``reason`` says supersedes ``own_id``
        — only when the reason also says so (:data:`_SUPERSEDED_RE`); else None."""
        text = str(reason or "")
        if not self.team or not _SUPERSEDED_RE.search(text):
            return None
        for m in re.finditer(rf"\b{re.escape(self.team)}-(\d+)\b", text, re.IGNORECASE):
            ident = f"{self.team}-{m.group(1)}".upper()
            if ident != str(own_id or "").upper():
                return ident
        return None

    def _relate_duplicate(self, task_id: str, issue_id: str, canonical: str) -> bool:
        nodes = self._nodes_by_id([canonical])
        target = next((n.get("id") for n in nodes or () if isinstance(n, dict)
                       and str(n.get("identifier") or "").upper() == canonical.upper()), None)
        if not target:
            log.warning("linear: %s marked Duplicate, but %s could not be read for the "
                        "relation", task_id, canonical)
            return False
        try:
            rel = self._call(CREATE_RELATION_MUTATION, {"input": {
                "issueId": issue_id, "relatedIssueId": target, "type": "duplicate",
            }}, cache=False)
            ok = bool((rel.get("issueRelationCreate") or {}).get("success"))
        except LinearError as e:
            log.warning("linear: %s marked Duplicate, but the relation to %s failed: %s",
                        task_id, canonical, e)
            return False
        if not ok:
            log.warning("linear: %s marked Duplicate, but the relation to %s was refused",
                        task_id, canonical)
        return ok

    def _transition_state(self, edge: str) -> dict | None:
        """The team state ``edge`` moves an issue to: the configured (or default) NAME, else
        the state TYPE's natural pick — In Progress = the first ``started`` state, In Review
        = the last one (only when there are two), Canceled/Duplicate = the first
        ``canceled`` one. None (logged) when the team has nothing that fits."""
        team = self._team()
        if team is None:
            return None
        states = team[1]
        name = self.transition_states.get(edge, "")
        named = [s for s in states if str(s.get("name", "")).lower() == name.lower()]
        if named:
            return named[0]
        by_pos = sorted(states, key=lambda s: s.get("position") or 0)
        started = [s for s in by_pos if s.get("type") == "started"]
        canceled = [s for s in by_pos if s.get("type") == "canceled"]
        pick = None
        if edge == "in_progress" and started:
            pick = started[0]
        elif edge == "in_review" and len(started) > 1:
            pick = started[-1]
        elif edge in ("canceled", "duplicate") and canceled:
            pick = canceled[0]
        if pick is None:
            _report_once((self.team, f"state:{edge}"),
                         f"linear: team {self.team} has no state named {name!r} (nor one of "
                         f"the type it falls back to) — chela will not write the {edge} edge")
        return pick

    def archive_issue(self, issue_id: str, label: str = "") -> bool:
        """``issueArchive`` one issue. True on success; logs and returns False on any
        failure — an unarchived issue is the sweep's to retry, never a crash."""
        try:
            data = self._call(ARCHIVE_MUTATION, {"id": issue_id}, cache=False)
        except LinearError as e:
            log.warning("linear: could not archive %s: %s", label or issue_id, e)
            return False
        ok = bool((data.get("issueArchive") or {}).get("success"))
        if not ok:
            log.warning("linear: archiving %s was refused", label or issue_id)
        return ok

    def archive_sweep(self, *, force: bool = False) -> int | None:
        """Apply the ``keep_done`` rule (CMX-8): of the team's finished (completed/canceled)
        but unarchived issues — whoever closed them — keep the ``keep_done`` most recently
        finished and archive the older ones, oldest first. Open issues are never archived
        and never count toward ``keep_done``. Then publish the team's non-archived count
        for ``chela doctor``. Throttled to once per :data:`ARCHIVE_SWEEP_INTERVAL_SECONDS`
        per team unless ``force``. Returns how many it archived, or None when it did not
        run or could not read."""
        if self.config_error:
            return None
        now = time.monotonic()
        last = _last_sweep.get(self.team)
        if not force and last is not None and now - last < ARCHIVE_SWEEP_INTERVAL_SECONDS:
            return None
        _last_sweep[self.team] = now
        try:
            # Uncached: a sweep right after close_tasks must see the issues it just closed.
            nodes = self._paged(SWEEP_QUERY, {"team": self.team}, cache=False)
        except LinearError as e:
            log.warning("linear: archive sweep could not read team %s: %s", self.team, e)
            return None
        finished = [
            n for n in nodes
            if isinstance(n, dict) and n.get("id") and not n.get("archivedAt")
            and isinstance(n.get("state"), dict)
            and n["state"].get("type") in TERMINAL_STATE_TYPES
        ]
        # Newest first; the number breaks a tie so the order never depends on the page.
        finished.sort(key=lambda n: (_finished_at(n), n.get("number") or 0), reverse=True)
        older = finished[self.keep_done:]
        archived = 0
        for node in reversed(older):              # oldest first: a capped sweep takes those
            if archived >= ARCHIVES_PER_SWEEP:
                break
            if self.archive_issue(node["id"], str(node.get("identifier") or "")):
                archived += 1
        remaining = len(nodes) - archived
        _publish_count(self.team, remaining)
        if over_issue_warning(remaining):
            log.warning(
                "linear: team %s has %d non-archived issues — the free plan stops at %d. "
                "Lower `keep_done` (now %d), or archive or delete issues, before new ones "
                "are refused.",
                self.team, remaining, ISSUE_CAP, self.keep_done,
            )
        if archived:
            log.info("linear: archived %d finished issue(s) in team %s (keeping the newest "
                     "%d)", archived, self.team, self.keep_done)
        return archived

    def create_issue(self, title: str, description: str = "", priority: int = 0,
                     blocked_by=()) -> dict:
        """📐 CMX-6 — create one issue in this team, in its first configured READY state,
        and a ``blocks`` relation from each of ``blocked_by`` (identifiers of this team).

        Returns ``{"identifier", "url", "title", "warnings"}``. Raises :class:`LinearError`
        when nothing was created (a bad input, an unknown blocker, Linear refused) — the
        caller keeps the typed brief. A relation that fails AFTER the issue exists is a
        warning, not an error: the issue is real, and creating it twice would be worse.
        Never a sub-issue: no ``parentId`` is ever sent."""
        title = (title or "").strip()
        if not title:
            raise LinearError("input", "a title is required")
        if isinstance(priority, bool) or priority not in PRIORITIES:
            raise LinearError("input", "priority must be 0 (none) to 4 (low)")
        blockers = [str(b).strip() for b in (blocked_by or ()) if str(b).strip()]
        if self.config_error:
            raise LinearError("config", self.config_error)
        foreign = [b for b in blockers if self._number_of(b) is None]
        if foreign:
            raise LinearError("input", f"not an issue of team {self.team}: {foreign[0]}")
        blocker_ids: dict[str, str] = {}
        if blockers:
            nodes = self._nodes_by_id(blockers)
            if nodes is None:
                raise LinearError("network", "could not read the blocking issues")
            found = {str(n.get("identifier") or "").upper(): n.get("id")
                     for n in nodes if isinstance(n, dict) and n.get("id")}
            for b in blockers:
                if b.upper() not in found:
                    raise LinearError("input", f"no such issue: {b}")
                blocker_ids[b.upper()] = found[b.upper()]
        team = self._team()
        if team is None:
            raise LinearError("network", f"could not read team {self.team}")
        team_id, states = team
        ready = {str(s.get("name", "")).lower(): s["id"] for s in states}
        state_id = next((ready[n.lower()] for n in self.ready_states if n.lower() in ready),
                        None)
        if state_id is None:
            raise LinearError("config", f"team {self.team} has no state named "
                                        f"{' / '.join(self.ready_states)}")
        data = self._call(CREATE_ISSUE_MUTATION, {"input": {
            "teamId": team_id, "title": title, "description": description or "",
            "priority": priority, "stateId": state_id,
        }}, cache=False)
        created = data.get("issueCreate") or {}
        issue = created.get("issue") if isinstance(created, dict) else None
        if not (isinstance(created, dict) and created.get("success") and isinstance(issue, dict)
                and issue.get("id") and issue.get("identifier")):
            raise LinearError("graphql", "Linear did not create the issue")
        warnings = []
        for ident, bid in blocker_ids.items():
            try:
                rel = self._call(CREATE_RELATION_MUTATION, {"input": {
                    "issueId": bid, "relatedIssueId": issue["id"], "type": "blocks",
                }}, cache=False)
                ok = bool((rel.get("issueRelationCreate") or {}).get("success"))
            except LinearError as e:
                log.warning("linear: %s created, but %s→%s failed: %s",
                            issue["identifier"], ident, issue["identifier"], e)
                ok = False
            if not ok:
                warnings.append(f"could not mark {issue['identifier']} blocked by {ident}")
        return {"identifier": issue["identifier"], "url": str(issue.get("url") or ""),
                "title": str(issue.get("title") or title), "warnings": warnings}

    # --- helpers -------------------------------------------------------------------------

    def _team(self) -> tuple[str, list[dict]] | None:
        """``(team id, workflow states)`` for this team, or None on a failed read."""
        try:
            data = self._call(TEAM_STATES_QUERY, {"team": self.team})
        except LinearError as e:
            log.warning("linear: could not read team %s's workflow states: %s", self.team, e)
            return None
        teams = ((data.get("teams") or {}).get("nodes")) or []
        team = teams[0] if teams and isinstance(teams[0], dict) else {}
        states = ((team.get("states") or {}).get("nodes")) or []
        if not team.get("id"):
            return None
        return team["id"], [s for s in states if isinstance(s, dict) and s.get("id")]

    def _done_state_id(self) -> str | None:
        team = self._team()
        if team is None:
            return None
        states = team[1]
        if self.done_state:
            named = [s for s in states if str(s.get("name", "")).lower() == self.done_state.lower()]
            if named:
                return named[0]["id"]
            log.warning("linear: team %s has no state named %r", self.team, self.done_state)
            return None
        completed = sorted((s for s in states if s.get("type") == "completed"),
                           key=lambda s: s.get("position") or 0)
        if not completed:
            log.warning("linear: team %s has no state of type `completed`", self.team)
            return None
        return completed[0]["id"]

    def _number_of(self, ident: object) -> int | None:
        m = re.fullmatch(rf"{re.escape(self.team)}-(\d+)", str(ident or "").strip(),
                         re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _task(self, node: object) -> Task | None:
        """One Linear issue → a :class:`Task`, or None for a malformed record."""
        if not isinstance(node, dict):
            return None
        ident, number, title = node.get("identifier"), node.get("number"), node.get("title")
        if not isinstance(ident, str) or not ident or not isinstance(title, str):
            return None
        try:
            number = int(number)
        except (TypeError, ValueError):
            return None
        state = node.get("state")
        if not isinstance(state, dict):
            return None
        description = node.get("description")
        labels = [
            lbl.get("name") for lbl in ((node.get("labels") or {}).get("nodes") or [])
            if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)
        ]
        risk = highest_risk(lbl[len("risk:"):] for lbl in labels
                            if lbl.lower().startswith("risk:"))
        terminal = None
        if state.get("type") == "canceled":
            terminal = "canceled"
        elif state.get("type") == "completed" or node.get("archivedAt"):
            terminal = "done"
        return apply_risk(Task(
            id=ident,
            title=title.strip(),
            file="",
            line_number=number,
            raw=str(node.get("url") or ident),
            url=_issue_url(node.get("url")),
            body=description.strip() if isinstance(description, str) and description.strip()
            else None,
            depends=tuple(b["identifier"] for b in _blockers(node)),
            task_number=number,
            branch=_branch_for(ident, title, node.get("branchName")),
            terminal_state=terminal,
            tracker_state=str(state.get("name") or "") or None,
            # CMX-430: the reconcile acts on `state`. A terminal (done/canceled/archived)
            # Linear issue is POSITIVELY closed, never left reading as open.
            state="closed" if terminal else "open",
        ), risk, "label")

    @staticmethod
    def _sorted(tasks: list[Task], nodes: list[dict]) -> list[Task]:
        """Claim order: priority (Urgent 1 → Low 4, "No priority" 0 LAST), then Linear's
        manual ``sortOrder`` (ascending — the order the board shows), then the number."""
        meta = {n.get("identifier"): n for n in nodes if isinstance(n, dict)}

        def key(t: Task):
            n = meta.get(t.id, {})
            try:
                prio = int(n.get("priority") or 0)
            except (TypeError, ValueError):
                prio = 0
            try:
                order = float(n.get("sortOrder") or 0.0)
            except (TypeError, ValueError):
                order = 0.0
            return (prio if prio > 0 else 5, order, t.task_number or 0)

        return sorted(tasks, key=key)


def _issue_url(url: object) -> str | None:
    """CMX-5: the issue's web URL as Linear returned it, or None. Only an ``https://`` URL
    is kept — the dashboard puts it in an ``href``, and anything else (a missing field, a
    ``javascript:`` string) must render no link at all rather than a guessed or unsafe one."""
    if isinstance(url, str) and url.strip().lower().startswith("https://"):
        return url.strip()
    return None


def _blockers(node: dict) -> list[dict]:
    """``blockedBy``: the issues with a ``blocks`` relation pointing AT this one (Linear
    stores it on the blocker, so it shows up in this issue's ``inverseRelations``).
    ``identifier`` is a placeholder no task can ever have for a relation whose issue could not be read — a blocker
    that can never be satisfied, so the task is held (fail closed)."""
    out = []
    rels = ((node.get("inverseRelations") or {}).get("nodes")) or []
    for rel in rels:
        if not isinstance(rel, dict) or rel.get("type") != "blocks":
            continue
        issue = rel.get("issue")
        if not isinstance(issue, dict) or not isinstance(issue.get("identifier"), str):
            out.append({"identifier": "?unreadable-blocker", "done": False})
            continue
        state = issue.get("state") if isinstance(issue.get("state"), dict) else {}
        out.append({"identifier": issue["identifier"],
                    "done": state.get("type") == "completed"})
    return out
