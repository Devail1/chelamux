"""OKF serializer — export chela's fleet knowledge as an Open Knowledge Format bundle.

Turns chela's runtime state (dispatcher ``runs``, scheduled ``tasks``, live agent
windows, the projects they touch) into a portable `Open Knowledge Format
<https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf>`_ (OKF
v0.1) bundle: a directory of typed markdown files with YAML frontmatter, plus the
reserved ``index.md`` (per-directory listing) and ``log.md`` (date-grouped
history) files. See ``docs/OKF.md`` for the design.

Conformance is deliberately small: every non-reserved ``.md`` carries frontmatter
with a non-empty ``type``. We emit the recommended soft fields too (``title``,
``description``, ``resource``, ``tags``, ``timestamp``) and extension keys; a
consumer must tolerate unknown keys / broken links / missing optionals.

⚠️ Boundary (see ``docs/OKF.md`` → Security / exposure): the *bundle is local
fleet data and is never published.* The default output lives outside the repo
(``~/.chela/knowledge/``); exporting into a git working tree is flagged.

Pure serializer: state in → files out. No daemon, no new deps (``pyyaml`` is
already required for WORKFLOW.md parsing).
"""
from __future__ import annotations

import logging
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from chela import discovery, dispatcher, scheduler, transcripts
from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

OKF_VERSION = "0.1"
DEFAULT_OUT = CHELA_DIR / "knowledge"

# OKF type names for chela's concepts (see docs/OKF.md producer table).
TYPE_RUN = "Dispatch Run"
TYPE_SCHEDULE = "Scheduled Task"
TYPE_AGENT = "Agent"
TYPE_PROJECT = "Project"

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# Small serialization helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    """Filesystem-safe stem for a concept filename (never empty)."""
    s = _SLUG_RE.sub("-", (value or "").strip()).strip("-.")
    return s or "unnamed"


def _excerpt(text: str | None, limit: int = 160) -> str:
    """First meaningful line of a prose blob, condensed to a one-line summary.

    Used to turn an agent's latest recap into its ``description`` so a glance card
    reads as *what the agent is doing* rather than boilerplate. Skips blank and
    markdown-heading lines; collapses whitespace; truncates on a word boundary.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):   # skip blanks + markdown headings → reach the prose
            continue
        line = re.sub(r"\s+", " ", line)
        if len(line) <= limit:
            return line
        return line[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return ""


def _frontmatter(meta: dict) -> str:
    """Render an OKF frontmatter block, dropping empty optional fields.

    ``type`` is always kept (it's the one required field); other keys are
    omitted when their value is empty so the bundle stays terse.
    """
    clean = {k: v for k, v in meta.items() if k == "type" or v not in (None, "", [], {})}
    body = yaml.safe_dump(clean, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{body}---\n"


def _rel(from_rel: str, to_rel: str) -> str:
    """Bundle-relative markdown link from one file to another (both repo-root-relative).

    Relative (``../runs/x.md``) rather than absolute (``/runs/x.md``) so links
    resolve under ``file://`` for the portable viewer too.
    """
    rel = posixpath.relpath(to_rel, posixpath.dirname(from_rel))
    return rel if rel.startswith(".") else f"./{rel}"


def _doc(meta: dict, *body: str, citations: list[str] | None = None) -> str:
    """Assemble a concept file: frontmatter + body sections + optional citations."""
    parts = [_frontmatter(meta), ""]
    parts.extend(s for s in body if s)
    if citations:
        parts.append("\n# Citations\n")
        parts.extend(f"- {c}" for c in citations)
    return "\n".join(parts).rstrip() + "\n"


def _index(title: str, description: str, entries: list[str], *, root: bool = False) -> str:
    """A reserved ``index.md`` — a progressive-disclosure listing.

    Per spec, index files carry NO frontmatter except the bundle-root index,
    which is the only place ``okf_version`` is declared.
    """
    head = ""
    if root:
        head = _frontmatter({
            "okf_version": OKF_VERSION,
            "title": title,
            "description": description,
            "timestamp": _now(),
        }) + "\n"
    lines = [head, f"# {title}\n", description, ""]
    lines.extend(entries or ["_(empty)_"])
    return "\n".join(ln for ln in lines if ln is not None).rstrip() + "\n"


def _listing(title: str, url: str, desc: str = "") -> str:
    """One ``index.md`` entry: ``* [Title](url) - description`` (reserved format)."""
    line = f"* [{title}]({url})"
    return f"{line} - {desc}" if desc else line


def _within_git_repo(path: Path) -> Path | None:
    """The nearest ancestor that is a git working tree, or None.

    Used to warn when an export would land inside version control — the bundle
    is private fleet data and must not be committed (docs/OKF.md → Security).
    """
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


# ---------------------------------------------------------------------------
# Producers — one per OKF type
# ---------------------------------------------------------------------------

def _run_doc(run: dict) -> str:
    title = run.get("title") or run.get("task_id") or "run"
    status = run.get("status") or "unknown"
    workflow = Path(run.get("workflow_path") or "").name
    window = run.get("window_name") or ""
    body = [f"**Status:** `{status}` · attempt {run.get('attempt', 1)}"]
    if workflow:
        body.append(f"**Workflow:** `{workflow}`")
    if run.get("branch_name"):
        body.append(f"**Branch:** `{run['branch_name']}`")
    if run.get("pr_url"):
        body.append(f"**PR:** {run['pr_url']}" + (f" ({run['pr_state']})" if run.get("pr_state") else ""))
    if window:
        body.append(f"**Agent:** [{window}]({_rel('runs/x.md', f'agents/{_slug(window)}.md')})")
    if run.get("last_error"):
        body.append(f"\n> error: {run['last_error']}")
    meta = {
        "type": TYPE_RUN,
        "title": title,
        "description": f"{status} — {workflow}" if workflow else status,
        "resource": run.get("pr_url") or "",
        "timestamp": run.get("ended_at") or run.get("started_at") or "",
        "tags": [t for t in (status, workflow) if t],
        # extension keys (consumers preserve unknown keys)
        "status": status,
        "workflow_path": run.get("workflow_path") or "",
        "window_name": window,
        "branch_name": run.get("branch_name") or "",
        "pr_state": run.get("pr_state") or "",
        "started_at": run.get("started_at") or "",
        "ended_at": run.get("ended_at") or "",
    }
    citations = [f"`{run['worktree_path']}`"] if run.get("worktree_path") else None
    return _doc(meta, "\n".join(body), citations=citations)


def _schedule_doc(task) -> str:
    first_line = (task.prompt or "").strip().splitlines()[0] if task.prompt else ""
    title = f"{task.agent_name}: {task.schedule_type} {task.schedule_value}"
    body = [
        f"**Agent:** [{task.agent_name}]({_rel('schedules/x.md', f'agents/{_slug(task.agent_name)}.md')})",
        f"**Schedule:** `{task.schedule_type}` = `{task.schedule_value}` · "
        f"{'enabled' if task.enabled else 'disabled'}",
        f"**Last run:** {task.last_run or '—'} · **Next run:** {task.next_run or '—'}",
        "\n## Prompt\n",
        (task.prompt or "").strip() or "_(empty)_",
    ]
    meta = {
        "type": TYPE_SCHEDULE,
        "title": title,
        "description": first_line[:140],
        "timestamp": task.next_run or "",
        "tags": [t for t in (task.schedule_type, task.agent_name) if t],
        "agent_name": task.agent_name,
        "schedule_type": task.schedule_type,
        "schedule_value": task.schedule_value,
        "enabled": bool(task.enabled),
        "last_run": task.last_run or "",
        "next_run": task.next_run or "",
    }
    return _doc(meta, "\n".join(body))


def _agent_doc(name: str, wid: str, runs_by_window: dict[str, list[dict]]) -> tuple[str, str | None]:
    """Return (markdown, cwd). cwd is surfaced so the caller can build projects."""
    self_rel = f"agents/{_slug(name)}.md"
    cwd = discovery.get_window_cwd(name)
    recap = None
    pr = None
    tpath = transcripts._resolve_agent_transcript(name)
    if tpath:
        try:
            recap = transcripts.latest_recap(tpath)
            pr = transcripts.latest_pr(tpath)
        except OSError:
            log.debug("Could not read transcript for agent %s", name)

    body = [f"**Window:** `{wid}`" + (f" · **CWD:** `{cwd}`" if cwd else "")]
    if cwd:
        proj = Path(cwd).name
        body.append(f"**Project:** [{proj}]({_rel(self_rel, f'projects/{_slug(proj)}.md')})")
    if pr:
        body.append(f"**Latest PR:** {pr.url}")
    if recap:
        body.append("\n## Latest recap\n")
        body.append(recap.strip())

    my_runs = runs_by_window.get(name, [])
    if my_runs:
        body.append("\n## Runs\n")
        body.extend(
            _listing(
                r.get("title") or r.get("task_id") or "run",
                _rel(self_rel, f"runs/{_slug(r['task_id'])}.md"),
                r.get("status") or "",
            )
            for r in my_runs
        )

    project = Path(cwd).name if cwd else ""
    # description = what the agent is actually doing (its latest recap), so a
    # glance card reads as insight; fall back to a plain locator when there's no
    # transcript (e.g. a bare shell window).
    description = _excerpt(recap) or (
        f"agent window {name}" + (f" @ {project}" if project else ""))
    meta = {
        "type": TYPE_AGENT,
        "title": name,
        "description": description,
        "resource": f"file://{cwd}" if cwd else "",
        "timestamp": _now(),
        "tags": ["agent"],
        "window_id": wid,
        "cwd": cwd or "",
        # extension keys (consumers preserve unknown keys) — let the viewer
        # surface the agent's project + latest PR without parsing the body.
        "project": project,
        "pr_url": pr.url if pr else "",
    }
    citations = [f"`{tpath}`"] if tpath else None
    return _doc(meta, "\n".join(body), citations=citations), cwd


def _project_doc(name: str, path: str, agents: list[str], runs: list[dict]) -> str:
    self_rel = f"projects/{_slug(name)}.md"
    body = [f"**Path:** `{path}`" if path else ""]
    if agents:
        body.append("\n## Agents\n")
        body.extend(_listing(a, _rel(self_rel, f"agents/{_slug(a)}.md")) for a in sorted(set(agents)))
    if runs:
        body.append("\n## Runs\n")
        body.extend(
            _listing(
                r.get("title") or r.get("task_id") or "run",
                _rel(self_rel, f"runs/{_slug(r['task_id'])}.md"),
                r.get("status") or "",
            )
            for r in runs
        )
    n_agents = len(set(agents))
    n_runs = len(runs)
    rollup = " · ".join(filter(None, [
        f"{n_agents} agent{'s' if n_agents != 1 else ''}" if n_agents else "",
        f"{n_runs} run{'s' if n_runs != 1 else ''}" if n_runs else "",
    ])) or (f"project at {path}" if path else f"project {name}")
    meta = {
        "type": TYPE_PROJECT,
        "title": name,
        "description": rollup,
        "resource": f"file://{path}" if path else "",
        "timestamp": _now(),
        "tags": ["project"],
        "path": path or "",
        "agent_count": n_agents,
        "run_count": n_runs,
    }
    return _doc(meta, "\n".join(body))


# ---------------------------------------------------------------------------
# log.md — date-grouped fleet activity, newest first
# ---------------------------------------------------------------------------

def _log_md(runs: list[dict]) -> str:
    """Build the reserved ``log.md``: ``## YYYY-MM-DD`` headings, newest first."""
    events: list[tuple[str, str, str]] = []  # (iso_ts, day, line)
    for r in runs:
        ts = r.get("ended_at") or r.get("started_at")
        if not ts:
            continue
        day = ts[:10]
        verb = "finished" if r.get("ended_at") else "started"
        title = r.get("title") or r.get("task_id") or "run"
        link = f"runs/{_slug(r['task_id'])}.md"
        events.append((ts, day, f"- {verb} **[{title}]({link})** — `{r.get('status') or '?'}`"))

    events.sort(key=lambda e: e[0], reverse=True)
    out = ["# Activity log\n", "Fleet activity, newest first.\n"]
    current_day = None
    for _ts, day, line in events:
        if day != current_day:
            out.append(f"\n## {day}\n")
            current_day = day
        out.append(line)
    if not events:
        out.append("_(no activity recorded)_")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def export_bundle(out_dir: Path | None = None, since: str | None = None) -> dict:
    """Export the full OKF bundle. Returns a summary dict of what was written.

    ``since`` (ISO date/datetime) filters runs (and therefore the activity log)
    to those started on/after it; agents/schedules/projects reflect current
    state regardless.
    """
    out = Path(out_dir) if out_dir else DEFAULT_OUT
    out = out.expanduser()

    repo = _within_git_repo(out)
    if repo:
        log.warning(
            "OKF export target %s is inside a git repo (%s) — the bundle is "
            "PRIVATE fleet data and must not be committed (docs/OKF.md → Security).",
            out, repo,
        )

    # --- gather state ---
    runs = dispatcher.list_runs()
    if since:
        runs = [r for r in runs if (r.get("started_at") or "") >= since]
    tasks = scheduler.list_tasks()
    windows = discovery.get_all_windows()  # {name: wid}

    runs_by_window: dict[str, list[dict]] = {}
    for r in runs:
        if r.get("window_name"):
            runs_by_window.setdefault(r["window_name"], []).append(r)

    # --- write concept files ---
    written = {"runs": 0, "schedules": 0, "agents": 0, "projects": 0}
    projects: dict[str, dict] = {}  # name -> {path, agents:set, runs:list}

    def _project(name: str, path: str = "") -> dict:
        p = projects.setdefault(name, {"path": "", "agents": set(), "runs": []})
        if path and not p["path"]:
            p["path"] = path
        return p

    (out / "runs").mkdir(parents=True, exist_ok=True)
    run_entries = []
    for r in runs:
        fname = f"{_slug(r['task_id'])}.md"
        (out / "runs" / fname).write_text(_run_doc(r), encoding="utf-8")
        run_entries.append(_listing(
            r.get("title") or r["task_id"], f"./{fname}", r.get("status") or "",
        ))
        written["runs"] += 1
        # attribute the run to a project via its workflow path
        wf = r.get("workflow_path")
        if wf:
            pname = Path(wf).parent.name or Path(wf).stem
            _project(pname)["runs"].append(r)

    (out / "schedules").mkdir(parents=True, exist_ok=True)
    sched_entries = []
    for t in tasks:
        fname = f"{t.id}.md"
        (out / "schedules" / fname).write_text(_schedule_doc(t), encoding="utf-8")
        sched_entries.append(_listing(
            f"{t.agent_name}: {t.schedule_value}", f"./{fname}",
            "enabled" if t.enabled else "disabled",
        ))
        written["schedules"] += 1

    (out / "agents").mkdir(parents=True, exist_ok=True)
    agent_entries = []
    for name, wid in sorted(windows.items()):
        md, cwd = _agent_doc(name, wid, runs_by_window)
        (out / "agents" / f"{_slug(name)}.md").write_text(md, encoding="utf-8")
        agent_entries.append(_listing(name, f"./{_slug(name)}.md", f"window {wid}"))
        written["agents"] += 1
        if cwd:
            _project(Path(cwd).name, cwd)["agents"].add(name)

    (out / "projects").mkdir(parents=True, exist_ok=True)
    proj_entries = []
    for name, data in sorted(projects.items()):
        md = _project_doc(name, data["path"], list(data["agents"]), data["runs"])
        (out / "projects" / f"{_slug(name)}.md").write_text(md, encoding="utf-8")
        proj_entries.append(_listing(name, f"./{_slug(name)}.md", data["path"]))
        written["projects"] += 1

    # --- reserved files ---
    (out / "runs" / "index.md").write_text(
        _index("Dispatch Runs", "Work-item dispatcher runs.", run_entries), encoding="utf-8")
    (out / "schedules" / "index.md").write_text(
        _index("Scheduled Tasks", "Time-based scheduled prompts.", sched_entries), encoding="utf-8")
    (out / "agents" / "index.md").write_text(
        _index("Agents", "Live agent windows.", agent_entries), encoding="utf-8")
    (out / "projects" / "index.md").write_text(
        _index("Projects", "Repos the fleet works in.", proj_entries), encoding="utf-8")

    (out / "log.md").write_text(_log_md(runs), encoding="utf-8")

    root_entries = [
        _listing("Agents", "./agents/index.md", f"{written['agents']} live"),
        _listing("Dispatch Runs", "./runs/index.md", f"{written['runs']} recorded"),
        _listing("Scheduled Tasks", "./schedules/index.md", f"{written['schedules']} tasks"),
        _listing("Projects", "./projects/index.md", f"{written['projects']} repos"),
        _listing("Activity log", "./log.md", "date-grouped, newest first"),
    ]
    (out / "index.md").write_text(
        _index(
            "chela fleet knowledge",
            "chela's accumulated fleet knowledge as an OKF v0.1 bundle.",
            root_entries, root=True,
        ),
        encoding="utf-8",
    )

    summary = {"out": str(out), "okf_version": OKF_VERSION, **written, "since": since or ""}
    log.info("OKF export → %s (%s)", out, ", ".join(f"{k}={v}" for k, v in written.items()))
    return summary


# ---------------------------------------------------------------------------
# Reader — the consumer half: parse a bundle back for the viewer.
#
# Pure functions, root in → JSON-able dicts out (no Flask, no chela runtime
# state), so they serve both the embedded dashboard routes and the portable
# viewer's data model, and stay unit-testable against a hand-written bundle.
#
# Per the OKF spec a consumer MUST be liberal: tolerate missing/broken
# frontmatter (treat as no metadata), unknown ``type`` (pass through), broken
# links (render dangling, never raise), and preserve unknown keys.
# ---------------------------------------------------------------------------

RESERVED = {"index.md", "log.md"}

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def parse_doc(text: str) -> tuple[dict, str]:
    """Split an OKF markdown file into ``(frontmatter, body)``.

    A file with no / blank / unparseable frontmatter yields ``({}, text)`` — the
    consumer treats it as an untyped concept rather than failing (spec: tolerate
    missing optionals).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, m.group(2)


def _title_from_path(rel: str) -> str:
    """Derive a display title from a filename when frontmatter has no ``title``."""
    return posixpath.splitext(posixpath.basename(rel))[0]


def _is_bundle_md_link(href: str) -> bool:
    """True for an in-bundle markdown link (skip http(s)/mailto/anchors/non-md)."""
    if not href or href.startswith(("http://", "https://", "mailto:", "#", "//")):
        return False
    return href.split("#", 1)[0].endswith(".md")


def _resolve_link(from_rel: str, href: str) -> str:
    """Resolve a markdown link to a bundle-root-relative posix path.

    Handles both relative (``../agents/x.md``) and absolute bundle links
    (``/agents/x.md``); strips any ``#anchor``.
    """
    href = href.split("#", 1)[0]
    if href.startswith("/"):
        return href.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), href))


def _concept_rel_paths(root: Path):
    """Yield ``(rel_posix, Path)`` for every non-reserved ``.md`` in the bundle."""
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if posixpath.basename(rel) in RESERVED:
            continue
        yield rel, p


def _safe_concept_path(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` for reading, refusing escapes / non-md.

    Path-traversal guard: the dashboard routes pass an untrusted ``?path=``, so a
    ``../../etc/passwd`` (or absolute) value must never read outside the bundle.
    Raises ``ValueError`` on an unsafe / non-``.md`` path, ``FileNotFoundError``
    when the (safe) target is missing.
    """
    rel = (rel or "").lstrip("/")
    if not rel.endswith(".md") or "\x00" in rel:
        raise ValueError("not a markdown concept path")
    root = root.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path escapes bundle")
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target


def _summary(rel: str, fm: dict) -> dict:
    """A lightweight concept card for tree/search listings (no body).

    Carries a few soft/extension fields (``resource``, ``project``, ``pr_url``)
    so the glance feed can render *what an agent is doing* and group agents under
    their project without a second per-concept fetch.
    """
    return {
        "path": rel,
        "title": fm.get("title") or _title_from_path(rel),
        "type": fm.get("type") or "",
        "description": fm.get("description") or "",
        "timestamp": fm.get("timestamp") or "",
        "tags": fm.get("tags") if isinstance(fm.get("tags"), list) else [],
        "resource": fm.get("resource") or "",
        "project": fm.get("project") or "",
        "pr_url": fm.get("pr_url") or "",
    }


def read_tree(root: Path) -> dict:
    """Browse + glance data: concepts grouped by directory, counts by type, log.

    One call powers both the glance overview (counts / freshest by timestamp /
    activity log) and the browse pane (``dirs``). Missing bundle → empty shape
    with ``exported: False`` so the viewer can show an export CTA instead of an
    error.
    """
    root = Path(root)
    if not (root / "index.md").exists():
        return {"exported": False, "okf_version": "", "total": 0,
                "counts": {}, "dirs": {}, "log": ""}

    root_fm, _ = parse_doc((root / "index.md").read_text(encoding="utf-8"))
    dirs: dict[str, list] = {}
    counts: dict[str, int] = {}
    total = 0
    for rel, p in _concept_rel_paths(root):
        fm, _ = parse_doc(p.read_text(encoding="utf-8"))
        card = _summary(rel, fm)
        dirs.setdefault(posixpath.dirname(rel) or ".", []).append(card)
        counts[card["type"] or "(untyped)"] = counts.get(card["type"] or "(untyped)", 0) + 1
        total += 1

    log_path = root / "log.md"
    log_body = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return {
        "exported": True,
        "okf_version": root_fm.get("okf_version") or OKF_VERSION,
        "total": total,
        "counts": counts,
        "dirs": dirs,
        "log": log_body,
    }


def read_concept(root: Path, rel: str) -> dict:
    """One concept: frontmatter + raw body + outbound links + computed backlinks.

    Backlinks (what links *to* this concept) are the headline feature — they
    can't be seen by ``ls``-ing the bundle. Computed by inverting the link graph:
    scan every other concept's body for a markdown link that resolves to ``rel``.
    Bundle is small; a full scan per open is fine.
    """
    root = Path(root)
    target = _safe_concept_path(root, rel)
    rel = target.relative_to(root.resolve()).as_posix()
    fm, body = parse_doc(target.read_text(encoding="utf-8"))

    outbound = []
    for title, href in _LINK_RE.findall(body):
        if not _is_bundle_md_link(href):
            continue
        tgt = _resolve_link(rel, href)
        outbound.append({
            "title": title, "path": tgt,
            "exists": (root / tgt).is_file(),
        })

    backlinks = []
    for other_rel, p in _concept_rel_paths(root):
        if other_rel == rel:
            continue
        _, obody = parse_doc(p.read_text(encoding="utf-8"))
        ofm = None
        for title, href in _LINK_RE.findall(obody):
            if _is_bundle_md_link(href) and _resolve_link(other_rel, href) == rel:
                if ofm is None:
                    ofm, _ = parse_doc(p.read_text(encoding="utf-8"))
                backlinks.append({
                    "path": other_rel,
                    "title": ofm.get("title") or _title_from_path(other_rel),
                    "type": ofm.get("type") or "",
                    "context": title,
                })
                break  # one backlink per source concept

    return {
        "path": rel,
        "title": fm.get("title") or _title_from_path(rel),
        "type": fm.get("type") or "",
        "frontmatter": fm,
        "body": body,
        "outbound": outbound,
        "backlinks": backlinks,
    }


def read_search(root: Path, q: str = "", type_: str = "", tag: str = "") -> list[dict]:
    """Full-text-ish search over frontmatter + body, filterable by type / tag.

    Substring match (case-insensitive) across title / description / tags / type /
    body — local, no index needed for a bundle this size. Title/description hits
    rank above body-only hits. Returns concept cards (+ a body snippet on a body
    hit). The embedded viewer may later swap in semantic search via ``mem_index``;
    this keeps a zero-dependency baseline shared with the portable viewer.
    """
    root = Path(root)
    ql = (q or "").strip().lower()
    type_l = (type_ or "").strip().lower()
    tag_l = (tag or "").strip().lower()
    results = []
    for rel, p in _concept_rel_paths(root):
        fm, body = parse_doc(p.read_text(encoding="utf-8"))
        card = _summary(rel, fm)
        if type_l and card["type"].lower() != type_l:
            continue
        if tag_l and tag_l not in [str(t).lower() for t in card["tags"]]:
            continue
        meta_blob = " ".join([
            card["title"], card["description"], card["type"],
            " ".join(str(t) for t in card["tags"]),
        ]).lower()
        snippet = ""
        rank = -1
        if not ql:
            rank = 0
        elif ql in meta_blob:
            rank = 2
        elif ql in body.lower():
            rank = 1
            idx = body.lower().index(ql)
            start = max(0, idx - 40)
            snippet = ("…" if start else "") + body[start:idx + len(ql) + 40].strip().replace("\n", " ")
        if rank < 0:
            continue
        results.append({**card, "rank": rank, "snippet": snippet})
    results.sort(key=lambda r: (-r["rank"], r["title"].lower()))
    return results


def read_graph(root: Path) -> dict:
    """Concept nodes + link edges — the "graph-shaped, not just tree-shaped" view.

    Nodes are concepts; a directed edge ``a → b`` exists when concept ``a``'s body
    has a markdown link resolving to concept ``b`` (broken links are dropped —
    only edges between two real concepts are emitted). Edges are deduped.
    """
    root = Path(root)
    nodes = []
    known = set()
    for rel, p in _concept_rel_paths(root):
        fm, _ = parse_doc(p.read_text(encoding="utf-8"))
        nodes.append({
            "id": rel,
            "title": fm.get("title") or _title_from_path(rel),
            "type": fm.get("type") or "",
        })
        known.add(rel)

    edges = []
    seen = set()
    for rel, p in _concept_rel_paths(root):
        _, body = parse_doc(p.read_text(encoding="utf-8"))
        for _title, href in _LINK_RE.findall(body):
            if not _is_bundle_md_link(href):
                continue
            tgt = _resolve_link(rel, href)
            if tgt in known and tgt != rel and (rel, tgt) not in seen:
                seen.add((rel, tgt))
                edges.append({"source": rel, "target": tgt})
    return {"nodes": nodes, "edges": edges}
