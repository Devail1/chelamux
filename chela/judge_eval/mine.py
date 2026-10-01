"""Mine judge-eval cases from the repo's own PR history.

Every judge verdict is a PR comment (``chela.judge.block_body`` / ``comment_body``). A
BLOCKED one quotes each surviving mutation verbatim — guard, file, and a ``- before`` /
``+ after`` diff — which is exactly an experiment the live judge proposed that the suite
could not catch. This module parses those back, pins each to the head it was judged on
(the PR's newest commit at or before the comment), and emits one case per PR: its FIRST
verdict, before any rework round could reshape the code (or the PR text) around it.

⛔ PUBLIC REPO. Only this repo's own public PR comments and commits go in. A finding whose
text carries anything that looks private — a home-directory path, a session link, a
token-shaped string — is dropped, not scrubbed: a scrubbed anchor no longer locates code.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from chela import envutil
from chela.judge_eval import dataset as ds

JUDGE_HEADER = "## ⚖️ THE JUDGE"
BLOCKED_MARK = "SURVIVED DELIBERATE CORRUPTION"
CLEAN_MARK = "every guard held"
CANNOT_VERIFY_MARK = "CANNOT VERIFY"

_FINDING_RE = re.compile(
    r"^### \d+\. \[(?P<kind>MUTATION|WIRING)\] (?P<guard>.+?)\n\n"
    r"\*\*File:\*\* `(?P<file>[^`]+)`\n\n"
    r"```diff\n(?P<diff>.*?)\n```",
    re.M | re.S,
)

# Anything that must never reach a public dataset. Checked on every string of a finding.
PRIVATE_RE = re.compile(
    r"/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|claude\.ai/code/session|"
    r"\bghp_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bsk-ant-[A-Za-z0-9-]{10,}|"
    r"\bxox[abp]-[A-Za-z0-9-]{10,}|\b\d{8,10}:[A-Za-z0-9_-]{30,}"
)


@dataclass
class Finding:
    guard: str
    file: str
    kind: str
    before: str
    after: str


def verdict_of(body: str) -> str:
    """``blocked`` / ``clean`` / ``cannot_verify`` for a judge verdict comment, else ``""``."""
    head = body.lstrip().split("\n", 1)[0]
    if not head.startswith(JUDGE_HEADER):
        return ""
    if BLOCKED_MARK in head:
        return "blocked"
    if CLEAN_MARK in head:
        return "clean"
    if CANNOT_VERIFY_MARK in head:
        return "cannot_verify"
    return ""


def _split_diff(diff: str) -> tuple[str, str]:
    """Reverse ``block_body``'s ``- line`` / ``+ line`` rendering back to (before, after)."""
    before, after = [], []
    for line in diff.split("\n"):
        if line.startswith("- ") or line == "-":
            before.append(line[2:])
        elif line.startswith("+ ") or line == "+":
            after.append(line[2:])
    return "\n".join(before), "\n".join(after)


def parse_findings(body: str) -> list[Finding]:
    """Every SURVIVED experiment quoted in a blocked verdict, in order."""
    out = []
    for m in _FINDING_RE.finditer(body):
        before, after = _split_diff(m.group("diff"))
        out.append(Finding(guard=m.group("guard").strip(), file=m.group("file").strip(),
                           kind="wiring" if m.group("kind") == "WIRING" else "mutation",
                           before=before, after=after))
    return out


def is_private(*texts: str) -> bool:
    return any(PRIVATE_RE.search(t or "") for t in texts)


def head_at(commits: list[dict], when: str) -> str:
    """The newest PR commit committed at or before ``when`` (ISO-8601, UTC ``Z``) — the head
    the judge saw. ``commits`` is the GitHub ``pulls/N/commits`` list, oldest first."""
    best = ""
    for c in commits:
        date = ((c.get("commit") or {}).get("committer") or {}).get("date") or ""
        if date and date <= when:
            best = c.get("sha") or best
    return best


# --- gh ----------------------------------------------------------------------------


def _gh_json(*args: str, paginate: bool = False):
    cmd = ["gh", "api", *(["--paginate", "--slurp"] if paginate else []), *args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                         env=envutil.child_env())
    if out.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} failed: {out.stderr.strip()[:300]}")
    data = json.loads(out.stdout or "null")
    if paginate:        # --slurp wraps each page in a list
        return [row for page in data for row in page]
    return data


def fetch_judge_comments(since: str) -> list[dict]:
    rows = _gh_json(f"repos/{{owner}}/{{repo}}/issues/comments?per_page=100&since={since}",
                    paginate=True)
    out = []
    for r in rows:
        verdict = verdict_of(r.get("body") or "")
        if verdict:
            out.append({"pr": int(r["issue_url"].rsplit("/", 1)[-1]), "verdict": verdict,
                        "created_at": r["created_at"], "body": r["body"]})
    return out


# --- building cases ----------------------------------------------------------------


def first_verdicts(comments: list[dict], min_pr: int) -> dict[int, dict]:
    """Per PR, its FIRST blocked-or-clean verdict (a cannot-verify judged nothing)."""
    firsts: dict[int, dict] = {}
    for c in sorted(comments, key=lambda c: c["created_at"]):
        if c["pr"] < min_pr or c["verdict"] not in ("blocked", "clean"):
            continue
        firsts.setdefault(c["pr"], c)
    return firsts


def build_case(repo: Path, pr: int, verdict: dict, *, log=print) -> ds.Case | None:
    meta = _gh_json(f"repos/{{owner}}/{{repo}}/pulls/{pr}")
    commits = _gh_json(f"repos/{{owner}}/{{repo}}/pulls/{pr}/commits?per_page=100",
                       paginate=True)
    head = head_at(commits, verdict["created_at"])
    if not head or not ds.ensure_commit(repo, head, pr):
        log(f"  #{pr}: judged head not reachable — skipped")
        return None
    base_ref = (meta.get("base") or {}).get("ref") or "dev"
    base = ds.merge_base(repo, head, f"origin/{base_ref}")
    if not base:
        log(f"  #{pr}: no merge-base with origin/{base_ref} — skipped")
        return None
    files = ds.changed_files(repo, base, head)
    targets, dropped = [], 0
    for i, f in enumerate(parse_findings(verdict["body"]) if verdict["verdict"] == "blocked"
                          else [], 1):
        content = ds.show_file(repo, head, f.file)
        if (is_private(f.guard, f.file, f.before, f.after) or content is None
                or not f.before or content.count(f.before) != 1):
            dropped += 1        # private, or its anchor does not locate code at this head
            continue
        targets.append(ds.Target(id=f"pr{pr}-f{i}", file=f.file, before=f.before,
                                 after=f.after, source=ds.HISTORICAL, guard=f.guard,
                                 kind=f.kind))
    title = meta.get("title") or ""
    if is_private(title):
        log(f"  #{pr}: title fails the privacy check — skipped")
        return None
    note = f"{dropped} finding(s) dropped (private, or anchor not unique at head)" if dropped else ""
    return ds.Case(
        id=f"pr{pr}", pr=pr, kind=ds.HISTORICAL, title=title, base_sha=base, head_sha=head,
        split=ds.split_for(pr), verdict=verdict["verdict"], changed_files=files,
        test_files=[p for p in files if ds.is_test_path(p)], targets=targets, note=note,
    )


def load_seeds(repo: Path, path: str | Path = ds.SEEDS_PATH) -> list[ds.Case]:
    """Seeded cases: a merged PR's squash commit, plus ONE known real regression as the target.

    ``data/seeds.jsonl`` rows: ``{slug, pr, merge_sha, file, before, after, why}`` plus a
    ``decoy_file/decoy_before/decoy_after`` NEGATIVE CONTROL. The case is
    ``merge_sha^..merge_sha`` (exactly that PR's diff); every ``before`` must occur exactly
    once in its file at ``merge_sha``.

    ⛔ The decoy is a line in a file the PR never touches, scored exactly like the seed. No
    sane plan for this PR reaches it, so a decoy hit rate above ~0% means the matcher (or
    the dataset) is broken, not that the judge is good.
    """
    cases = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        sha = s["merge_sha"]
        base = f"{sha}^"
        base_sha = ds._git(repo, "rev-parse", base).stdout.strip()
        files = ds.changed_files(repo, base_sha, sha)
        cases.append(ds.Case(
            id=f"seed-{s['slug']}", pr=int(s["pr"]), kind=ds.SEEDED, title=s["title"],
            base_sha=base_sha, head_sha=sha, split=ds.split_for(int(s["pr"])),
            verdict="merged", changed_files=files,
            test_files=[p for p in files if ds.is_test_path(p)],
            targets=[ds.Target(id=f"seed-{s['slug']}", file=s["file"], before=s["before"],
                               after=s["after"], source=ds.SEEDED, guard=s["why"],
                               label=ds.REAL, label_shape="seeded",
                               label_rationale=s["why"], label_by="seed"),
                     ds.Target(id=f"decoy-{s['slug']}", file=s["decoy_file"],
                               before=s["decoy_before"], after=s["decoy_after"],
                               source=ds.DECOY, guard="negative control: a file this PR never touches")],
            note=s.get("note", ""),
        ))
    return cases


def mine(repo: Path, *, min_pr: int, since: str, existing: list[ds.Case],
         log=print) -> list[ds.Case]:
    """Rebuild the dataset: historical cases from gh + the seeds, carrying every existing
    label forward by target id (a re-mine never silently throws a label away)."""
    labels = {t.id: t for c in existing for t in c.targets if t.label}
    comments = fetch_judge_comments(since)
    firsts = first_verdicts(comments, min_pr)
    log(f"{len(comments)} judge verdict comment(s); {len(firsts)} PR(s) from #{min_pr} with a "
        "first blocked/clean verdict")
    cases: list[ds.Case] = []
    for pr in sorted(firsts, reverse=True):
        case = build_case(repo, pr, firsts[pr], log=log)
        if case:
            cases.append(case)
    cases.extend(load_seeds(repo))
    for c in cases:
        for t in c.targets:
            old = labels.get(t.id)
            if old and old.before == t.before and old.after == t.after and not t.label:
                t.label, t.label_shape = old.label, old.label_shape
                t.label_rationale, t.label_by = old.label_rationale, old.label_by
    return cases
