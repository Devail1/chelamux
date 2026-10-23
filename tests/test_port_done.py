"""CMX-249 (CMX-212 P35): the React port's definition of done, as tests.

``docs/FRONTEND_PORT_PLAN.md`` §6 lists what "the entire dashboard is React" means. Each item
is held by a test so it cannot quietly regress; ``docs/FRONTEND.md`` ("The port's definition
of done") maps every item to the test holding it, and :func:`test_every_definition_of_done_item_names_tests_that_exist`
checks that map. This file holds the items no other suite does:

* §6.1 no legacy view module is left under ``static/js/``;
* §6.2 ``templates/index.html`` is the Vite entry and nothing else;
* §6.4 every node suite the port deleted is recorded as ported (to a test that exists) or
  retired with a reason;
* §6.6 no dual implementation;
* §6.7 every row of the plan's §3 iframe boundary has its test;
* §6.9 every UX decision of the plan's §7 names a test that exists;
* and P35's sweep: no ``style.css`` rule styles a class or id that no source renders.

§6.3 is ``src/iframeBridge.test.ts`` + ``tests/browser/pane_keys.test.mjs``, §6.8 is
``src/uikit.test.ts``, §5's empty emoji allowlist is ``tests/no_emoji.test.mjs``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "chela" / "dashboard"
SRC = DASH / "frontend" / "src"
LEGACY_JS = DASH / "static" / "js"
TEMPLATE = DASH / "templates" / "index.html"
STYLE = DASH / "static" / "style.css"
PLAN = ROOT / "docs" / "FRONTEND_PORT_PLAN.md"
FRONTEND_MD = ROOT / "docs" / "FRONTEND.md"

# The 43 legacy modules of the plan's §1 inventory, by base name.
LEGACY_MODULES = frozenset("""
    main sse util views viewreg header orchestrator windowid approvals accessreq statushealth
    resources resourcesmodel runtoast version updateflow decisions decisionsmodel feedmodel work
    kanban kanbanhiddenmodel kanbanlanemodel kanbanlinearmodel dispatcher runstate taskmodal
    taskmodalmodel knowledge newtask schedules usage cost concurrencymodel launcher agents
    sidebarmodel terminals wallmodel wire diffpanel diffpanelmodel nav
""".split())

# What may stay in static/js/: three pure models that src/ imports (docs/FRONTEND.md, "The
# stack"). TypeScript already, no DOM: moving them into src/ is a rename with no behaviour.
PURE_MODELS = frozenset({"wallmodel.ts", "windowid.ts", "wire.ts"})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of the markdown section headed ``heading``, up to the next heading of any level."""
    start = text.index(heading)
    nxt = re.search(r"^#{1,6} ", text[start + len(heading):], re.M)
    return text[start: start + len(heading) + nxt.start()] if nxt else text[start:]


def _table_rows(section: str) -> list[list[str]]:
    """The cells of each body row of the first markdown table in ``section``."""
    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in rows[2:]]


# --- §6.1 -----------------------------------------------------------------------------------

def test_the_legacy_inventory_is_the_plans_43_modules():
    assert len(LEGACY_MODULES) == 43
    assert {"terminals", "nav", "util", "main"} <= LEGACY_MODULES


def test_static_js_holds_only_the_pure_models():
    files = {p.name for p in LEGACY_JS.iterdir() if p.is_file()}
    assert files == PURE_MODELS, f"a legacy module is back in static/js: {sorted(files - PURE_MODELS)}"
    assert not [p for p in LEGACY_JS.rglob("*") if p.suffix in (".js", ".mjs")]


@pytest.mark.parametrize("name", sorted(PURE_MODELS))
def test_a_kept_model_is_pure(name):
    code = "\n".join(ln for ln in _read(LEGACY_JS / name).splitlines() if not ln.lstrip().startswith("//"))
    for banned in ("document.", "window.", "localStorage", "fetch(", "addEventListener"):
        assert banned not in code, f"{name} reaches the page ({banned}); it is no longer a pure model"


def test_what_stays_vanilla_by_section_3_is_still_there():
    for rel in ("static/collab/e2e.js", "static/term-reconnect.js", "static/term-upload.js",
                "static/term-touch.css", "static/vendor/gridstack-all.js"):
        assert (DASH / rel).is_file(), rel


# --- §6.2 -----------------------------------------------------------------------------------

def entry_problems(html: str) -> list[str]:
    """Why ``html`` is not the Vite entry page of plan §6.2 (empty when it is)."""
    out = []
    bare = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    bare = re.sub(r"\{%.*?%\}|\{#.*?#\}", "", bare, flags=re.S)
    for m in re.finditer(r"<[a-zA-Z][^>]*?\s(on[a-z]+)\s*=", bare):
        out.append(f"inline handler {m.group(1)}=")
    if re.search(r"\bchela\.\w", bare):
        out.append("a chela. call")
    body = bare[bare.index("<body"):]
    tags = [(m.group(1).lower(), m.group(2)) for m in re.finditer(r"<([a-zA-Z][\w-]*)([^>]*)>", body)][1:]
    want = ["script", "script", "div", "script", "script"]
    if [t for t, _ in tags] != want:
        out.append(f"body elements {[t for t, _ in tags]}, want {want}")
    else:
        if 'id="chela-boot"' not in tags[1][1] or 'type="application/json"' not in tags[1][1]:
            out.append("the second script is not the boot JSON block")
        if tags[2][1].strip() != 'id="root"':
            out.append('the one div is not <div id="root">')
        if 'type="module"' not in tags[4][1]:
            out.append("the last script is not the module entry")
    return out


def test_index_html_is_the_vite_entry():
    assert entry_problems(_read(TEMPLATE)) == []


@pytest.mark.parametrize("edit, why", [
    (lambda h: h.replace('<div id="root"></div>', '<div id="root" onclick="x()"></div>'), "inline handler onclick="),
    (lambda h: h.replace("</body>", '<button onkeydown = "f()">x</button></body>'), "inline handler onkeydown="),
    (lambda h: h.replace("</body>", "<script>chela.openPalette()</script></body>"), "a chela. call"),
    (lambda h: h.replace('<div id="root"></div>', '<div id="root"></div><div id="legacy"></div>'), "body elements"),
    (lambda h: h.replace('<div id="root"></div>', '<div id="app"></div>'), 'the one div is not <div id="root">'),
    (lambda h: h.replace('id="chela-boot"', 'id="boot"'), "the second script is not the boot JSON block"),
    (lambda h: h.replace('<script type="module"', "<script"), "the last script is not the module entry"),
])
def test_index_html_negative_controls(edit, why):
    html = _read(TEMPLATE)
    changed = edit(html)
    assert changed != html
    assert any(p.startswith(why) for p in entry_problems(changed)), entry_problems(changed)


def test_comments_and_jinja_are_not_the_page():
    html = _read(TEMPLATE).replace("</body>", "<!-- <b onclick=x>chela.old()</b> -->{# chela.x #}</body>")
    assert entry_problems(html) == []


# --- §6.4 -----------------------------------------------------------------------------------

def suite_table_problems(md: str, tests_dir: Path, src: Path) -> list[str]:
    """Why the FRONTEND.md table of deleted node suites does not hold (empty when it does)."""
    rows = _table_rows(_section(md, "### What happened to the node suites"))
    out = [] if rows else ["the table has no rows"]
    seen = set()
    for row in rows:
        if len(row) != 3:
            out.append(f"malformed row {row}")
            continue
        suite, _, outcome = row
        name = suite.strip("`")
        if name in seen:
            out.append(f"{name}: listed twice")
        seen.add(name)
        if (tests_dir / name).exists():
            out.append(f"{name}: still in tests/, so it was not ported or retired")
        homes = re.findall(r"`([^`]+\.test\.(?:ts|tsx|mjs))`", outcome)
        if outcome.startswith("ported:"):
            if not homes:
                out.append(f"{name}: ported, but names no test")
            for h in homes:
                path = (tests_dir.parent / h) if h.startswith("tests/") else (src / h)
                if not path.is_file():
                    out.append(f"{name}: its home {h} does not exist")
        elif outcome.startswith("retired:"):
            if len(outcome.split()) < 5:
                out.append(f"{name}: retired without a reason")
        else:
            out.append(f"{name}: outcome must start 'ported:' or 'retired:'")
    return out


def test_every_deleted_node_suite_is_ported_or_retired_with_a_reason():
    assert suite_table_problems(_read(FRONTEND_MD), ROOT / "tests", SRC) == []


def _suites_md(rows: str) -> str:
    return "### What happened to the node suites\n\n| Suite | Slice | Outcome |\n|---|---|---|\n" + rows + "\n## next\n"


@pytest.mark.parametrize("row, why", [
    ("| `gone.test.mjs` | x | ported: `features/nope/tests/Nope.test.tsx` |", "its home"),
    ("| `gone.test.mjs` | x | ported: nowhere |", "names no test"),
    ("| `gone.test.mjs` | x | retired: gone |", "without a reason"),
    ("| `gone.test.mjs` | x | moved |", "outcome must start"),
    ("| `no_emoji.test.mjs` | x | retired: the whole view it tested was deleted |", "still in tests/"),
])
def test_suite_table_negative_controls(row, why):
    problems = suite_table_problems(_suites_md(row), ROOT / "tests", SRC)
    assert any(why in p for p in problems), problems


def test_suite_table_positive_control():
    ok = ("| `gone.test.mjs` | x | ported: `boot.test.tsx`, `tests/no_emoji.test.mjs` |\n"
          "| `old.test.mjs` | x | retired: the whole view it tested was deleted |")
    assert suite_table_problems(_suites_md(ok), ROOT / "tests", SRC) == []
    assert suite_table_problems(_suites_md(ok + "\n" + ok.splitlines()[0]), ROOT / "tests", SRC) \
        == ["gone.test.mjs: listed twice"]


# --- §6.6 -----------------------------------------------------------------------------------

def _modules(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and "node_modules" not in p.parts
            and not p.name.endswith(".d.ts") and ".test." not in p.name]


def test_no_dual_implementation():
    ported = _modules(SRC) + _modules(LEGACY_JS)
    js = [p for p in ported if p.suffix in (".js", ".jsx", ".mjs")]
    assert js == [], "a JavaScript module in the TypeScript tree"
    stems: dict[str, list[Path]] = {}
    for p in ported:
        if p.suffix in (".ts", ".tsx") and p.stem != "index":
            stems.setdefault(p.stem, []).append(p)
    twins = {s: ps for s, ps in stems.items() if any(q.parent == LEGACY_JS for q in ps) and len(ps) > 1}
    assert twins == {}, "a static/js model and a src/ module share a base name"
    assert not [p for p in _modules(SRC) if "LegacyTab" in _read(p)]


# --- §6.7 -----------------------------------------------------------------------------------

def test_every_iframe_boundary_row_has_its_test():
    section = _section(_read(PLAN), "## 3. What stays vanilla")
    table = section[section.index("| Direction |"):]
    rows = [r for r in _table_rows(table) if r and r[0] in ("iframe → parent", "iframe reads parent",
                                                           "iframe reads storage", "parent → iframe")]
    assert len(rows) == 7
    spec = _read(SRC / "features" / "paneframe" / "tests" / "boundary.test.tsx")
    numbered = sorted(int(n) for n in re.findall(r"test\('§3 row (\d+),", spec))
    assert numbered == list(range(1, len(rows) + 1))


# --- §6.9 and the §6 map ----------------------------------------------------------------------

def held_by_problems(cell: str, root: Path) -> list[str]:
    """Why a *Held by* cell does not name tests that exist (empty when it does)."""
    pairs = re.findall(r'`([^`]+)` "([^"]+)"', cell)
    if not pairs:
        return ["names no test"]
    out = []
    for rel, title in pairs:
        path = root / rel
        if not path.is_file():
            out.append(f"{rel} does not exist")
        elif title not in _read(path):
            out.append(f"{rel} has no test {title!r}")
    return out


def test_every_ux_decision_names_a_test_that_exists():
    rows = _table_rows(_section(_read(PLAN), "## 7. UX decisions made during the port"))
    assert len(rows) >= 13
    bad = {f"{r[0]} {r[1]}: {r[2][:40]}": held_by_problems(r[4] if len(r) > 4 else "", ROOT) for r in rows}
    assert {k: v for k, v in bad.items() if v} == {}


@pytest.mark.parametrize("cell, why", [
    ("", "names no test"),
    ("tests/no_emoji.test.mjs", "names no test"),
    ('`tests/nope.test.mjs` "x"', "does not exist"),
    ('`tests/no_emoji.test.mjs` "a test nobody wrote"', "has no test"),
    ('`tests/no_emoji.test.mjs` "the legacy allowlist is empty"; `tests/nope.test.mjs` "x"', "does not exist"),
])
def test_held_by_negative_controls(cell, why):
    problems = held_by_problems(cell, ROOT)
    assert any(why in p for p in problems), problems


def test_held_by_positive_control():
    assert held_by_problems('`tests/no_emoji.test.mjs` "the legacy allowlist is empty"', ROOT) == []


def test_every_definition_of_done_item_names_tests_that_exist():
    rows = _table_rows(_section(_read(FRONTEND_MD), "## The port's definition of done"))
    assert [r[0] for r in rows] == [str(i) for i in range(1, 10)]
    for item, _, held in rows:
        paths = re.findall(r"`((?:tests|src)/[^`]+)`", held)
        assert paths, f"§6.{item} names no test"
        for rel in paths:
            path = ROOT / rel if rel.startswith("tests/") else SRC.parent / rel
            assert path.is_file(), f"§6.{item}: {rel} does not exist"


# --- the dead-CSS sweep -------------------------------------------------------------------------

# Class families a source builds from a template, so no source spells the full name. Each entry:
# the style.css names it covers (exact names, or a prefix ending in '-'), the source file and
# the template text in it that builds them.
BUILT = [
    (("gs-state-",), "frontend/src/features/panehead/lib/PaneHead.tsx", "gs-state-${"),
    (("kanban-col-",), "frontend/src/features/work/lib/Board.tsx", "kanban-col-${"),
    (("feed-cls-",), "frontend/src/features/decisions/lib/DecisionsDialog.tsx", "feed-cls-${"),
    (("decisions-chip-",), "frontend/src/features/decisions/lib/DecisionsDialog.tsx", "decisions-chip-${"),
    (("kb-up", "kb-down", "kb-left", "kb-right"), "frontend/src/features/keybar/lib/Keybar.tsx", "`kb-${"),
    (("kn-ul", "kn-ol"), "frontend/src/shared/markdown/markdown.ts", "kn-${kind}"),
    (("s-timing-rows",), "frontend/src/features/settings/lib/Knobs.tsx", "s-${kind}-rows"),
    (("panel-terminals",), "frontend/src/features/shell/views.ts", "panel-${"),
]


def css_names(css: str) -> set[str]:
    """Every class and id a selector in ``css`` names (declarations and comments dropped)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # An at-rule's prelude and its brace go (its rules stay), then each declaration block.
    css = re.sub(r"@[a-z-]+[^;{]*(\{|;)", ";", css)
    css = re.sub(r"\{[^{}]*\}", ";", css).replace("}", ";")
    css = re.sub(r"\[[^\]]*\]", "", css)
    return set(re.findall(r"(?<![\w-])[.#]([a-zA-Z_][\w-]*)", css))


def _covered(name: str, built: list) -> bool:
    return any(name == c or (c.endswith("-") and name.startswith(c)) for covers, _, _ in built for c in covers)


def unrendered(css: str, sources: str, built: list) -> list[str]:
    """The classes and ids ``css`` styles that ``sources`` never names and ``built`` does not build."""
    return sorted(n for n in css_names(css)
                  if not re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", sources) and not _covered(n, built))


def _sources() -> str:
    files = [p for p in _modules(SRC) if p.suffix in (".ts", ".tsx") and "tests" not in p.parts]
    files += list(LEGACY_JS.glob("*.ts")) + list((DASH / "static" / "collab").glob("*.js"))
    files += list((DASH / "static").glob("*.js")) + list(DASH.glob("*.py")) + list((DASH / "templates").glob("*.html"))
    return "\n".join(_uncommented(_read(p)) if p.suffix in (".ts", ".tsx", ".js") else _read(p) for p in files)


def _uncommented(code: str) -> str:
    """``code`` without its block comments and whole-line ``//`` comments: a class named only in
    prose (``// was .lane-head``) is not rendered."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("//"))


def test_no_style_rule_for_a_class_or_id_nothing_renders():
    assert unrendered(_read(STYLE), _sources(), BUILT) == []


@pytest.mark.parametrize("covers, rel, template", BUILT)
def test_each_built_family_is_still_built_and_still_styled(covers, rel, template):
    assert template in _read(DASH / rel), f"{rel} no longer builds {covers}: delete the entry and its CSS"
    names = css_names(_read(STYLE))
    assert any(_covered(n, [(covers, rel, template)]) for n in names), f"style.css no longer styles {covers}"


def test_css_names_reads_selectors_only():
    css = ("/* .commented */ .a, #b > .c:hover { color: #fff; background: url(x.png) }\n"
           "@media (max-width: 1.5em) { .d .e::after { content: '.f' } }\n"
           ".g[data-x='.h'] { }\n@container (min-width: 2px) { .i { } }")
    assert css_names(css) == {"a", "b", "c", "d", "e", "g", "i"}


def test_unrendered_negative_and_positive_controls():
    css = ".live { } .dead { } .gs-state-working { } .kb-up { } #root { }"
    built = [(("gs-state-",), "", ""), (("kb-up",), "", "")]
    assert unrendered(css, 'className="live" id="root"', built) == ["dead"]
    assert unrendered(css, 'className="live dead" id="root"', built) == []
    # a name that only appears inside a longer one is not rendered
    assert unrendered(".live { }", 'className="live-wire"', []) == ["live"]
    assert unrendered(".kb-down { }", "", built) == ["kb-down"]


def test_a_class_named_only_in_a_comment_is_not_rendered():
    src = "// the old .lane-head\n/* .lane-fold\n   too */\nconst a = 'live'; // trailing: still code\n"
    assert unrendered(".lane-head { } .lane-fold { } .live { }", _uncommented(src), []) == ["lane-fold", "lane-head"]
