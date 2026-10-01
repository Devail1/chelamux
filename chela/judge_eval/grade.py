"""Scoring one proposed experiment plan: validity, recall, and the contrived rate.

Cheapest viable grader first, always:

* **validity** and **recall** are fully PROGRAMMATIC. Validity runs the judge's own
  :func:`chela.judge.apply_mutation` + :func:`chela.judge.parse_check` on a scratch copy of
  the file at the case head — the exact refusal rules the live judge applies. Recall is a
  line-overlap test: does the experiment CHANGE a line the target's regression changes?
* **contrived** is programmatic where the mutation's shape decides it on its own (a single
  flipped operator is an ordinary edit; a new special case keyed on a literal is not), and
  an LLM grader with CHECKABLE CLAIMS for everything else — never a 1-5 score. The grader
  answers booleans and an enum per experiment, plus a verbatim quote of the code it says
  is changed; the quote is verified against ``before`` and an unverifiable answer is
  counted UNGRADED, not guessed. :func:`label_from_claims` turns the claims into a label
  deterministically, so the rule lives in code and in ``rubric.md``, not in a prompt.
"""
from __future__ import annotations

import difflib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chela import judge
from chela.judge_eval import dataset as ds

REALISTIC, CONTRIVED, UNGRADED = "realistic", "contrived", "ungraded"

RUBRIC_PATH = Path(__file__).parent / "rubric.md"
RUBRIC_VERSION = "rubric-v1"

SHAPES = (
    "flip_condition", "disable_check", "drop_call", "empty_value", "revert_callsite",
    "off_by_one", "swap_order", "narrow_set", "change_constant", "rewrite_logic",
    "special_case_injection", "targeted_literal", "other",
)
# Shapes that are contrived BY DEFINITION (rubric.md §2): the edit only exists to dodge or
# trip one specific test, so no ordinary future change produces it.
CONTRIVED_SHAPES = frozenset({"special_case_injection", "targeted_literal"})


def _norm_path(p: str) -> str:
    p = (p or "").strip()
    return p[2:] if p.startswith("./") else p


# --- validity ----------------------------------------------------------------------


def check_validity(exp: dict, content: str | None) -> tuple[bool, str]:
    """Does ``exp`` apply cleanly as a text edit to ``content`` (its file at the case head)?

    Reuses the judge's own mechanics on a scratch copy, so "valid" here means exactly "the
    live judge would have run it" — not an approximation of that."""
    parsed, why = judge.Experiment.parse(exp)
    if parsed is None:
        return False, f"malformed experiment: {why}"
    if content is None:
        return False, f"{parsed.file} does not exist at the case head"
    name = Path(_norm_path(parsed.file)).name or "file"
    with tempfile.TemporaryDirectory(prefix="chela-judge-eval-") as tmp:
        path = Path(tmp) / name
        path.write_text(content)
        applied, reason, _ = judge.apply_mutation(path, parsed.before, parsed.after)
        if not applied:
            return False, reason
        ok, detail = judge.parse_check(path)
        return (True, detail) if ok else (False, detail)


# --- recall ------------------------------------------------------------------------


def changed_lines(content: str, before: str, after: str) -> set[int]:
    """The 1-based lines of ``content`` a ``before → after`` edit changes, or ``set()`` if
    ``before`` does not occur exactly once (an edit that cannot be located reaches nothing).

    A pure insertion changes no existing line, so it is attributed to the line it lands on."""
    if not before or before == after or content.count(before) != 1:
        return set()
    first = content[: content.index(before)].count("\n") + 1
    b, a = before.split("\n"), after.split("\n")
    out: set[int] = set()
    for op, i1, i2, _j1, _j2 in difflib.SequenceMatcher(a=b, b=a, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if i1 == i2:
            out.add(first + min(i1, len(b) - 1))
        else:
            out.update(range(first + i1, first + i2))
    return out


def target_hit(target: ds.Target, experiments: list[dict], contents: dict[str, str | None]) -> bool:
    """Does any experiment change a line the target's regression changes, in the same file?

    ``contents`` maps a normalized path to the file's text at the case head."""
    tfile = _norm_path(target.file)
    tcontent = contents.get(tfile)
    if tcontent is None:
        return False
    want = changed_lines(tcontent, target.before, target.after)
    if not want:
        return False
    for exp in experiments:
        if _norm_path(exp.get("file", "")) != tfile:
            continue
        got = changed_lines(tcontent, exp.get("before") or "", exp.get("after") or "")
        if got & want:
            return True
    return False


# --- contrived: tier 1, programmatic -----------------------------------------------

_TOKEN_RE = re.compile(r"\w+|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s\w]+")
_FLIPS = {
    ("==", "!="), ("!=", "=="), ("===", "!=="), ("!==", "==="), ("<", "<="), ("<=", "<"),
    (">", ">="), (">=", ">"), ("<", ">"), (">", "<"), ("and", "or"), ("or", "and"),
    ("&&", "||"), ("||", "&&"), ("True", "False"), ("False", "True"), ("true", "false"),
    ("false", "true"), ("is", "is not"), ("in", "not in"), ("any", "all"), ("all", "any"),
}
_EMPTY = {"None", '""', "''", "[]", "{}", "()", "0", "null", "undefined", "False", "false",
          "set()", "dict()", "list()", "pass", "``"}
_DISABLE_PREFIXES = ("False and", "false &&", "0 and", "False and not", "(false &&")
_LITERAL_RE = re.compile(r"^(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\d+(?:\.\d+)?)$")


def _tokens(s: str) -> list[str]:
    return _TOKEN_RE.findall(s)


def classify_programmatic(exp: dict) -> tuple[str | None, str, str]:
    """``(label, shape, reason)`` when the mutation's SHAPE alone decides it, else
    ``(None, "", "")`` — the LLM tier's turn. Deliberately conservative: only shapes the
    rubric names as ordinary edits, or as contrived by definition, are decided here."""
    before, after = exp.get("before") or "", exp.get("after") or ""
    if (exp.get("kind") or "") == "wiring":
        return None, "", ""          # a revert can be plausible or surgical — needs reading
    b, a = _tokens(before), _tokens(after)
    ops = [o for o in difflib.SequenceMatcher(a=b, b=a, autojunk=False).get_opcodes()
           if o[0] != "equal"]
    if not ops:
        return None, "", ""
    added = [t for o in ops for t in a[o[3]:o[4]]]
    removed = [t for o in ops for t in b[o[1]:o[2]]]
    # A new equality test against a literal, where the original had none: the edit is a
    # special case aimed at one input — rubric §2, contrived by definition.
    if ({"==", "===", "!=", "!=="} & set(added)) and any(_LITERAL_RE.match(t) for t in added) \
            and not ({"==", "===", "!=", "!=="} & set(removed)):
        return CONTRIVED, "special_case_injection", "adds a comparison against a literal the original never made"
    if len(ops) == 1:
        op, i1, i2, j1, j2 = ops[0]
        old, new = " ".join(b[i1:i2]), " ".join(a[j1:j2])
        if op == "replace" and (old, new) in _FLIPS:
            return REALISTIC, "flip_condition", f"a single flipped operator ({old} → {new})"
        if op in ("insert", "delete") and (new or old) in ("not", "!"):
            return REALISTIC, "flip_condition", "a single negation added or removed"
        if op == "replace" and new in _EMPTY and old not in _EMPTY:
            return REALISTIC, "empty_value", f"a value emptied to {new}"
    if after.lstrip().startswith(("#", "//")) and not before.lstrip().startswith(("#", "//")):
        return REALISTIC, "drop_call", "the statement is commented out (a dropped call)"
    if any(p in after and p not in before for p in _DISABLE_PREFIXES):
        return REALISTIC, "disable_check", "the check is short-circuited off (a dropped guard)"
    return None, "", ""


# --- contrived: tier 2, an LLM with checkable claims -------------------------------

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "changed_code_quote": {"type": "string"},
                    "shape": {"type": "string", "enum": list(SHAPES)},
                    "ordinary_edit": {"type": "boolean"},
                    "requires_test_knowledge": {"type": "boolean"},
                    "main_path": {"type": "boolean"},
                    "rationale": {"type": "string"},
                },
                "required": ["index", "changed_code_quote", "shape", "ordinary_edit",
                             "requires_test_knowledge", "main_path", "rationale"],
            },
        },
    },
    "required": ["grades"],
}


def label_from_claims(claims: dict, before: str) -> tuple[str, str]:
    """The rubric's decision rule (``rubric.md`` §3), applied to one experiment's claims.

    ⛔ ``changed_code_quote`` must be a verbatim substring of ``before`` — a grader that
    cannot point at the code it judged has not judged it, and that is UNGRADED."""
    quote = (claims.get("changed_code_quote") or "").strip()
    if not quote or " ".join(quote.split()) not in " ".join(before.split()):
        return UNGRADED, "the grader's quote does not occur in the experiment's `before`"
    shape = claims.get("shape")
    if shape not in SHAPES:
        return UNGRADED, f"unknown shape {shape!r}"
    if shape in CONTRIVED_SHAPES:
        return CONTRIVED, f"shape {shape} is contrived by definition"
    if claims.get("requires_test_knowledge") is True:
        return CONTRIVED, "only someone who knows what the test asserts would write it"
    if claims.get("ordinary_edit") is False:
        return CONTRIVED, "no ordinary future edit produces it"
    if claims.get("ordinary_edit") is True and claims.get("requires_test_knowledge") is False:
        return REALISTIC, "an ordinary edit, no test knowledge needed"
    return UNGRADED, "claims missing"


def grader_prompt(items: list[dict], diff_excerpt: str) -> str:
    rubric = RUBRIC_PATH.read_text()
    lines = [
        "You grade proposed code MUTATIONS against a written rubric. Answer each claim "
        "literally; do not score. You have no tools and need none.",
        "", "# Rubric", "", rubric, "",
        "# The PR these mutations target (diff excerpt)", "", "```diff", diff_excerpt, "```",
        "", "# Mutations to grade", "",
    ]
    for it in items:
        lines += [f"## index {it['index']} — `{it.get('file', '')}` ({it.get('kind') or 'mutation'})",
                  f"guard: {it.get('guard', '')}", "", "before:", "```", it.get("before", ""),
                  "```", "after:", "```", it.get("after", ""), "```", ""]
    lines += ["Return one grade per index. `changed_code_quote` MUST be copied verbatim from "
              "that mutation's `before` — the exact code the mutation changes."]
    return "\n".join(lines)


def diff_excerpt(full_diff: str, files: set[str], limit: int = 20000) -> str:
    """The diff hunks for ``files`` only — what the grader needs to judge plausibility."""
    out, keep = [], False
    for line in full_diff.split("\n"):
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            keep = _norm_path(path) in files
        if keep:
            out.append(line)
    text = "\n".join(out)
    if len(text) > limit:
        text = text[:limit] + f"\n… [diff excerpt cut at {limit} chars; {len(text) - limit} more]"
    return text


@dataclass
class Grade:
    label: str
    shape: str
    reason: str
    tier: str                        # "programmatic" | "llm" | "none"
    flipped: bool = False            # the two LLM runs disagreed on the label


def grade_contrived(experiments: list[dict], runner, full_diff: str,
                    runs: int = 2) -> tuple[list[Grade], float, list[str]]:
    """Grade every experiment. Programmatic tier first; the rest go to ``runner`` ``runs``
    times (grader consistency) in ONE batched call per run. Returns (grades, cost, errors).

    The label is run 1's; a run-2 disagreement marks it ``flipped`` — reported, not hidden."""
    grades: list[Grade | None] = []
    pending = []
    for i, exp in enumerate(experiments):
        label, shape, reason = classify_programmatic(exp)
        if label:
            grades.append(Grade(label, shape, reason, "programmatic"))
        else:
            grades.append(None)
            pending.append({"index": i, **{k: exp.get(k, "") for k in
                                           ("guard", "file", "kind", "before", "after")}})
    cost, errors = 0.0, []
    if pending:
        files = {_norm_path(p["file"]) for p in pending}
        prompt = grader_prompt(pending, diff_excerpt(full_diff, files))
        per_run: list[dict[int, tuple[str, str, str]]] = []
        for _ in range(max(1, runs)):
            res = runner.run(prompt, cwd=None, schema=CLAIMS_SCHEMA, tools=())
            cost += res.cost_usd
            if res.error:
                errors.append(res.error)
                per_run.append({})
                continue
            got = {}
            for g in (res.structured or {}).get("grades") or []:
                idx = g.get("index")
                if not isinstance(idx, int) or not 0 <= idx < len(experiments):
                    continue
                label, why = label_from_claims(g, experiments[idx].get("before") or "")
                got[idx] = (label, g.get("shape") or "", f"{why}: {g.get('rationale', '')}".strip())
            per_run.append(got)
        for p in pending:
            idx = p["index"]
            first = per_run[0].get(idx) if per_run else None
            if not first:
                grades[idx] = Grade(UNGRADED, "", "the grader returned no grade", "none")
                continue
            flipped = any(r.get(idx) and r[idx][0] != first[0] for r in per_run[1:])
            grades[idx] = Grade(first[0], first[1], first[2], "llm", flipped=flipped)
    return [g for g in grades if g is not None], cost, errors


def parse_json_loose(text: str) -> dict | None:
    """The first JSON object in ``text`` — for a runner that returned prose around it."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None
