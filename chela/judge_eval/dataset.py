"""The judge-eval dataset: cases, targets, the train/test split, and the git plumbing that
rebuilds a case's tree and diff from the repo instead of storing them.

A **case** is one PR at one judged head: the diff ``base_sha...head_sha``, its changed and
test files, and its **targets** — the weaknesses a good experiment plan should reach:

* ``source="historical"`` — a mutation the live judge proposed on that head that SURVIVED
  the suite (it is in the PR's verdict comment). Each carries a rubric label, ``real`` or
  ``contrived`` (``rubric.md``). Only ``real`` ones count toward recall.
* ``source="seeded"`` — a known real regression from history, injected as a target on a
  merged PR's tree (``data/seeds.jsonl``). It is ``real`` by construction.
* ``source="decoy"`` — the NEGATIVE CONTROL on every seeded case: a line in a file that PR
  never touches. Scored like a seed; its hit rate must stay ~0%.

⛔ Diffs and file contents are NOT stored. They are rebuilt from git at run time (fetching
``pull/<N>/head`` when a squash-merged branch's commits are no longer local), so the
dataset stays small and every case stays reproducible against the public repo.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chela import envutil

DATA_DIR = Path(__file__).parent / "data"
CASES_PATH = DATA_DIR / "cases.jsonl"
SEEDS_PATH = DATA_DIR / "seeds.jsonl"

# ⛔ The split is a pure function of the PR number — never a column someone can edit to move
# a hard case out of the test set. ``split`` is stored on each case for readability, and a
# test pins stored == computed. Bumping the salt re-deals the split: do that only on
# purpose, and never after anyone has seen test-set results.
SPLIT_SALT = "chela-judge-eval-v1"
TEST_PERCENT = 30

TRAIN, TEST = "train", "test"
REAL, CONTRIVED = "real", "contrived"
HISTORICAL, SEEDED, DECOY = "historical", "seeded", "decoy"

GIT_TIMEOUT = 120


def split_for(pr: int) -> str:
    """``test`` for ~TEST_PERCENT% of PRs, ``train`` for the rest — by PR, so every round
    and every seed built on one PR lands on the same side."""
    h = int(hashlib.sha256(f"{SPLIT_SALT}:{int(pr)}".encode()).hexdigest(), 16)
    return TEST if h % 100 < TEST_PERCENT else TRAIN


@dataclass
class Target:
    """A weakness a proposed experiment can reach. ``before`` locates it in ``file`` at the
    case head (it must occur there exactly once); ``after`` is the regression itself."""
    id: str
    file: str
    before: str
    after: str
    source: str                     # HISTORICAL | SEEDED | DECOY
    guard: str = ""
    kind: str = "mutation"
    label: str = ""                 # REAL | CONTRIVED | "" (unlabelled)
    label_shape: str = ""
    label_rationale: str = ""
    label_by: str = ""

    @property
    def is_real(self) -> bool:
        return self.label == REAL


@dataclass
class Case:
    id: str
    pr: int
    kind: str                       # HISTORICAL | SEEDED
    title: str
    base_sha: str
    head_sha: str
    split: str
    verdict: str = ""               # the live judge's first verdict on head_sha
    changed_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    note: str = ""

    @property
    def real_targets(self) -> list[Target]:
        return [t for t in self.targets if t.is_real]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Case":
        raw = dict(raw)
        raw["targets"] = [Target(**t) for t in raw.get("targets") or []]
        return cls(**raw)


def load_cases(path: str | Path = CASES_PATH) -> list[Case]:
    p = Path(path)
    if not p.is_file():
        return []
    return [Case.from_dict(json.loads(line)) for line in p.read_text().splitlines()
            if line.strip()]


def save_cases(cases: list[Case], path: str | Path = CASES_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(c.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"
                         for c in cases))


# --- git ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=text,
        timeout=GIT_TIMEOUT, env=envutil.child_env(),
    )


def repo_root(start: str | Path = ".") -> Path:
    out = _git(Path(start), "rev-parse", "--show-toplevel")
    if out.returncode != 0:
        raise RuntimeError(f"{start} is not inside a git repository")
    return Path(out.stdout.strip())


def has_commit(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ensure_commit(repo: Path, sha: str, pr: int | None = None) -> bool:
    """Is ``sha`` available locally — fetching ``pull/<pr>/head`` if it is not?

    ⛔ Fetches into FETCH_HEAD only: no ref is created in the operator's repo."""
    if has_commit(repo, sha):
        return True
    if pr:
        _git(repo, "fetch", "--quiet", "origin", f"pull/{int(pr)}/head")
    return has_commit(repo, sha)


def show_file(repo: Path, sha: str, path: str) -> str | None:
    out = _git(repo, "show", f"{sha}:{path}")
    return out.stdout if out.returncode == 0 else None


def diff(repo: Path, base: str, head: str) -> str:
    return _git(repo, "diff", f"{base}...{head}").stdout


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", f"{base}...{head}").stdout
    return [line for line in out.splitlines() if line.strip()]


def commit_messages(repo: Path, base: str, head: str) -> str:
    """What the PR claimed at ``head``: its own commit messages, oldest first.

    ⛔ Deliberately NOT the PR body or its comments. Both can be edited after the judge ran
    (a rework round describes the very findings this eval scores), so either would leak the
    answer into the prompt. Commits up to the judged head cannot."""
    return _git(repo, "log", "--reverse", "--format=%B%n---", f"{base}..{head}").stdout


def merge_base(repo: Path, a: str, b: str) -> str:
    out = _git(repo, "merge-base", a, b)
    return out.stdout.strip() if out.returncode == 0 else ""


def materialize(repo: Path, sha: str, dest: Path) -> None:
    """Extract ``sha``'s tree into ``dest`` (no ``.git`` — the design step reads, it never
    runs git). ``git archive`` writes nothing into the repo."""
    out = _git(repo, "archive", "--format=tar", sha, text=False)
    if out.returncode != 0:
        raise RuntimeError(f"git archive {sha[:12]} failed: {out.stderr.decode(errors='replace')[:200]}")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(dest, filter="data")


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or "/tests/" in path or name.startswith("test_")
            or ".test." in name or name.endswith("_test.py"))
