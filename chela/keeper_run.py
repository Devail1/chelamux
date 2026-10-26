"""Keeper proposal run — the digest in, evidence-cited board changes out (CMX-347).

Keeper mode (CMX-345), slice 2. ``chela keeper run <WORKFLOW.md>`` builds the digest
(:mod:`chela.keeper`), asks ONE headless ``claude -p`` call (no tools) for JSON proposals,
and queues ONE inbox item for a person to approve or dismiss. Nothing is written to the
tracker until that person says so (``chela keeper approve|dismiss <batch>``).

**The model proposes; code decides what may be proposed.** :func:`validate` drops every
proposal that cites no evidence, cites a ref the digest does not hold, quotes text the
cited item does not contain, uses an action outside :data:`ACTIONS`, moves an issue to In
Progress from anywhere but Todo/Backlog, or calls an issue done without a MERGED PR in the
digest whose title names it. The model never sees the tracker and never writes to it.

**Every write is attributed**: a comment from "chela keeper" naming the approver, the
batch and the evidence. Every proposal and its outcome (``dropped``, ``proposed``,
``accepted``, ``rejected``, ``failed``) is a row in ``$CHELA_DIR/keeper/log.jsonl`` — the
trial's acceptance rate is read from that file.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from chela import config, keeper
from chela.workflow import WorkflowDef

ACTIONS = ("in_progress", "comment", "new_backlog_issue", "flag_stale", "done")
EVIDENCE_KINDS = ("commit", "pr", "prompt", "edit")
START_STATES = ("todo", "backlog")
DEFAULT_MODEL = "sonnet"
ACTOR = "chela keeper"

SCHEMA = {"type": "object", "required": ["proposals"], "properties": {"proposals": {
    "type": "array", "items": {"type": "object", "required": ["issue", "action", "evidence"],
                               "properties": {
        "issue": {"type": "string"}, "action": {"type": "string", "enum": list(ACTIONS)},
        "to_state": {"type": "string"}, "comment": {"type": "string"},
        "title": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "object",
                     "required": ["kind", "ref", "quote"], "properties": {
                         "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
                         "ref": {"type": "string"}, "quote": {"type": "string"}}}}}}}}}

PROMPT = """You keep a software project's issue board aligned with the work actually done.
Below is a digest of the last window: the open board and every piece of evidence, each with
a `kind` and a `ref`. Propose board changes ONLY where the evidence shows the board is wrong:
- in_progress: work has started on an issue that is still Todo or Backlog
- done: a MERGED pr names the issue
- comment: a short factual note an issue should carry (put it in `comment`)
- new_backlog_issue: work is happening that no issue covers (give `title`; `issue` = "")
- flag_stale: an In Progress issue nothing in the window touched
Every proposal cites evidence: copy `kind` and `ref` exactly as given, and `quote` a
verbatim fragment of that item's `text`. Proposals without such evidence are discarded.
Propose nothing when nothing is wrong. Return JSON only.

"""


def keeper_dir() -> Path:
    return Path(config.CHELA_DIR) / "keeper"


def log_path() -> Path:
    return keeper_dir() / "log.jsonl"


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def evidence_index(d: dict) -> dict[tuple[str, str], dict]:
    """Every citable item in the digest, by ``(kind, ref)``."""
    idx = {("commit", c["sha"]): {"text": c["subject"]} for c in d["commits"]["items"]}
    for p in d["prs"]["items"]:
        idx[("pr", f"#{p['number']}")] = {"text": p["title"], "event": p["event"]}
    for w in d["sessions"]["items"]:
        for i, p in enumerate(w["prompts"]["items"], 1):
            idx[("prompt", f"{w['wid']}.p{i}")] = {"text": p["text"]}
        for i, path in enumerate(w["edits"]["items"], 1):
            idx[("edit", f"{w['wid']}.e{i}")] = {"text": path}
    return idx


def _ref(kind, ref) -> str:
    ref = str(ref or "").strip()
    return f"#{ref}" if kind == "pr" and ref.isdigit() else ref


def _drop_reason(p, d: dict, idx: dict) -> str | None:
    """Why proposal ``p`` must not reach a person, or None when it may."""
    if not isinstance(p, dict):
        return "not an object"
    action, issue = p.get("action"), str(p.get("issue") or "").upper()
    if action not in ACTIONS:
        return f"action {action!r} is not allowed"
    evidence = p.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "no evidence"
    cited = []
    for e in evidence:
        if not isinstance(e, dict):
            return "evidence is not an object"
        item = idx.get((e.get("kind"), _ref(e.get("kind"), e.get("ref"))))
        if item is None:
            return f"evidence {e.get('kind')}:{e.get('ref')} is not in the digest"
        if not _norm(e.get("quote")) or _norm(e.get("quote")) not in _norm(item["text"]):
            return f"evidence {e.get('kind')}:{e.get('ref')} does not say that"
        cited.append((e["kind"], item))
    if action == "new_backlog_issue":
        return None if str(p.get("title") or "").strip() else "new issue without a title"
    state = {t["id"].upper(): t["state"] for t in d["board"]["items"]}.get(issue)
    if state is None:
        return f"{issue or 'no issue'} is not on the board"
    if action == "in_progress" and str(state).lower() not in START_STATES:
        return f"{issue} is {state}, not Todo or Backlog"
    if action == "comment" and not str(p.get("comment") or "").strip():
        return "comment without text"
    if action == "done" and not any(
            kind == "pr" and item.get("event") == "merged"
            and issue in keeper.issue_ids([item["text"]], d["tracker_key"])
            for kind, item in cited):
        return "done without a merged PR that names the issue"
    return None


def validate(proposals, d: dict) -> tuple[list[dict], list[dict]]:
    """``(kept, dropped)``; each dropped entry is ``{"proposal", "reason"}``."""
    idx, kept, dropped = evidence_index(d), [], []
    for p in proposals if isinstance(proposals, list) else []:
        why = _drop_reason(p, d, idx)
        if why:
            dropped.append({"proposal": p, "reason": why})
        else:
            kept.append({**p, "issue": str(p.get("issue") or "").upper()})
    return kept, dropped


def log(batch: str, outcome: str, proposal, **extra) -> None:
    row = {"ts": time.time(), "batch": batch, "outcome": outcome, "proposal": proposal, **extra}
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def model_for(wf: WorkflowDef, flag: str | None) -> str:
    return flag or str(wf.get("keeper", "model", default="") or "") or DEFAULT_MODEL


def ask_model(prompt: str, *, model: str, cwd: str):
    """The one model call. ``(proposals, error)`` — exactly one of them is None."""
    from chela.judge_eval.runner import ClaudeCLIRunner
    result = ClaudeCLIRunner(model, max_budget_usd=1.0, timeout=600).run(
        prompt, cwd=cwd, schema=SCHEMA, tools=())
    proposals = (result.structured or {}).get("proposals")
    if result.error or not isinstance(proposals, list):
        return None, result.error or "the model returned no proposals list"
    return proposals, None


def _prompt(d: dict) -> str:
    facts = {"board": d["board"]["items"],
             "recaps": [{"session": w["wid"], "recap": w["recap"]}
                        for w in d["sessions"]["items"] if w["recap"]],
             "evidence": [{"kind": k, "ref": r, **v} for (k, r), v in evidence_index(d).items()]}
    return PROMPT + json.dumps(facts, indent=1, default=str)


def _batch_path(batch: str, state: str = "pending") -> Path:
    return keeper_dir() / "batches" / f"{batch}.{state}.json"


def run(wf: WorkflowDef, *, since: int | None = None, model: str | None = None,
        asker=None) -> dict:
    """Digest → model → validate → log → one inbox item. The digest cursor advances only
    once the model answered, so a failed call leaves the window for the next run."""
    from chela import inbox
    d = keeper.build(wf, since=since)
    proposals, error = (asker or ask_model)(_prompt(d), model=model_for(wf, model), cwd=keeper.repo_roots(wf)[0])
    if error is not None:
        return {"error": error}
    if d["cursor"]:
        keeper.save_cursor(wf, d["cursor"])
    batch = secrets.token_hex(8)
    kept, dropped = validate(proposals, d)
    for x in dropped:
        log(batch, "dropped", x["proposal"], reason=x["reason"])
    for p in kept:
        log(batch, "proposed", p)
    out = {"batch": batch if kept else None, "proposals": kept, "dropped": len(dropped)}
    if kept:
        path = _batch_path(batch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"batch": batch, "workflow": str(wf.path),
                                    "proposals": kept, "ts": time.time()}), encoding="utf-8")
        out["notified"] = inbox.enqueue(
            "keeper_proposals",
            f"🗂️ keeper: {len(kept)} board change(s) proposed for {d['tracker_key']} "
            f"({len(dropped)} dropped) — chela keeper approve {batch} / dismiss {batch}",
            {"batch": batch, "workflow": str(wf.path), "proposals": kept,
             "dropped": len(dropped)})
    return out


def _attribution(p: dict, batch: str, by: str) -> str:
    cites = "; ".join(f"{e['kind']} {e['ref']}: “{e['quote']}”" for e in p["evidence"])
    return f"— {ACTOR} ({p['action']}, approved by {by}, batch {batch}). Evidence: {cites}"


def apply(source, p: dict, batch: str, by: str) -> str:
    """One approved proposal → its tracker write(s), each attributed. The outcome word of
    the write; ``set``/``already``/``struck`` mean it landed."""
    note = _attribution(p, batch, by)
    action, issue = p["action"], p["issue"]
    if action in ("comment", "flag_stale"):
        head = p.get("comment") or ("⚠️ Flagged stale" if action == "flag_stale" else "")
        return source.comment(issue, f"{head}\n\n{note}".strip())
    if action == "new_backlog_issue":
        created = source.create_issue(p["title"], description=p.get("comment") or "")
        return source.comment(created["identifier"], note)
    if action == "in_progress":
        result = source.transition(issue, "in_progress")
    else:
        result = source.close_tasks([issue]).get(issue, "missing")
    if result in ("set", "struck"):
        source.comment(issue, note)
    return result


def decide(batch: str, *, approve: bool, only: set[int] | None = None, by: str = "terminal",
           source=None) -> dict:
    """Approve (all, or the 1-based indexes in ``only``) or dismiss a pending batch. The
    proposals come from the stored batch, never from the caller; claiming the file by
    rename means a second decision on the same batch finds nothing to apply."""
    if not batch.isascii() or not batch.isalnum():
        return {"error": f"not a batch id: {batch!r}"}
    pending, done = _batch_path(batch), _batch_path(batch, "decided")
    try:
        os.replace(pending, done)
        rec = json.loads(done.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"no pending batch {batch} ({exc})"}
    if approve and source is None:
        from chela.sources import get_source
        from chela.workflow import load_workflow
        source = get_source(load_workflow(rec["workflow"]))
    results = []
    for i, p in enumerate(rec["proposals"], 1):
        if not approve or (only is not None and i not in only):
            log(batch, "rejected", p, by=by)
            results.append("rejected")
            continue
        try:
            result = apply(source, p, batch, by)
        except Exception as exc:              # noqa: BLE001 — one failed write never stops the rest
            result = f"failed: {exc}"
        ok = result in ("set", "already", "struck")
        log(batch, "accepted" if ok else "failed", p, by=by, result=result)
        results.append(result)
    return {"batch": batch, "results": results}
