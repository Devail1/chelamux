"""Doctor must read the manifest that RUNS, not the one chela writes.

`chela plugin` renders `$CHELA_DIR/plugin/hooks/hooks.json`. **No agent reads that file.**
`/plugin install` copies the plugin into Claude Code's cache, and that copy is what every
agent loads at startup. The two drifted — the rendered one raised `PermissionRequest` to
120s, the installed one still said 2 — so every gate hook was killed after two seconds, no
gate was ever held, the phone's answer buttons never appeared, and `chela doctor` printed
green all day, because it checked the file chela WRITES.

These tests pin the fix: the installed copy is found by DISCOVERY (so a version bump moves
the cache directory and it still resolves), a drift is an ERROR, and a copy that cannot be
found or cannot be read is *also* an ERROR — never a silent pass, which is the same bug one
level up.
"""
from __future__ import annotations

import json

import pytest

from chela import config, doctor, hooks, main

PORT = 5005


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated $CHELA_DIR and an isolated Claude Code config dir."""
    chela_dir = tmp_path / "chela"
    chela_dir.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    claude = tmp_path / "claude"
    (claude / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    # A dashboard IS listening on PORT — published, pid-checked, the way the real one does
    # it. The plugin facts render their expected manifest against the port that is really
    # bound (CMX-41), so the fixture has to bind one.
    config.publish_dashboard_port(PORT)
    return tmp_path


def _render(port: int = PORT):
    """What `chela plugin` writes — the manifest nobody reads."""
    return hooks.render_plugin(config.CHELA_DIR / "plugin", port=port)


def _install(spec: dict, version: str = "0.1.0", marketplace: str = "chela",
             register: bool = True):
    """A plugin copy where Claude Code puts one, and (optionally) its bookkeeping."""
    root = hooks.plugins_dir() / "cache" / marketplace / "chela" / version
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps(spec), encoding="utf-8")
    if register:
        registry = hooks.plugins_dir() / "installed_plugins.json"
        data = {"version": 2, "plugins": {}}
        if registry.exists():
            data = json.loads(registry.read_text())
        data["plugins"].setdefault(f"chela@{marketplace}", []).append(
            {"scope": "user", "installPath": str(root), "version": version})
        registry.write_text(json.dumps(data), encoding="utf-8")
    return root


def _register_marketplaces(*names: str) -> None:
    """Claude Code's own registry of marketplaces it currently knows about — the file
    `claude plugin marketplace remove` (or a cache sweep) can drop an entry from without
    touching `installed_plugins.json` or the cached manifest at all."""
    path = hooks.plugins_dir() / "known_marketplaces.json"
    path.write_text(json.dumps({name: {} for name in names}), encoding="utf-8")


def _stale(port: int = PORT) -> dict:
    """The manifest that hid all day: PermissionRequest killed after 2 seconds."""
    spec = hooks.hooks_spec(port)
    spec["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] = 2
    return spec


def _check(port: int = PORT) -> list[doctor.Finding]:
    """The two plugin facts, audited exactly as `chela doctor` audits them — through the
    registry, with no check of their own. `port` is what the dashboard has BOUND (the
    fixture publishes it), because that is the port the manifest must carry."""
    assert config.live_dashboard_port() == port
    return [
        finding
        for name in ("plugin.rendered", "plugin.installed")
        for f in [doctor.fact(name)]
        if f.applies()
        for finding in doctor.audit(f)
    ]


def _levels(findings, level):
    return [f for f in findings if f.level == level]


def _text(findings) -> str:
    return "\n".join(f"{f.title}\n{f.detail}" for f in findings)


# --- discovery --------------------------------------------------------------------

def test_the_installed_copy_is_discovered_not_constructed(env):
    _install(hooks.hooks_spec(PORT))
    copies = hooks.installed_plugins()
    assert len(copies) == 1
    assert copies[0].version == "0.1.0"
    assert copies[0].hooks == hooks.hooks_spec(PORT)
    assert copies[0].found_via == "installed_plugins.json"


def test_a_version_bump_still_resolves_the_right_cache_dir(env):
    """The cache path CONTAINS the version. A hardcoded path would check a directory that
    no longer exists — silently — the day `plugin.json` is bumped."""
    root = _install(hooks.hooks_spec(PORT), version="9.9.9")
    copies = hooks.installed_plugins()
    assert [c.root for c in copies] == [root]
    assert copies[0].version == "9.9.9"
    assert "9.9.9" in str(copies[0].manifest)


def test_the_cache_is_scanned_when_the_registry_is_unusable(env):
    """Claude Code's bookkeeping is its own and may change shape. Losing it must degrade
    to a scan, not to a pass."""
    root = _install(hooks.hooks_spec(PORT), version="2.0.0", register=False)
    (hooks.plugins_dir() / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    copies = hooks.installed_plugins()
    assert [c.root for c in copies] == [root]
    assert copies[0].found_via == "a scan of the plugin cache"


def test_manifest_drift_is_empty_when_they_agree(env):
    assert hooks.manifest_drift(hooks.hooks_spec(PORT), hooks.hooks_spec(PORT)) == []


def test_marketplace_is_read_from_the_registry(env):
    """`chela update` needs this to know which `claude plugin update <plugin>@<marketplace>`
    to run (CMX-186) — the marketplace name is whatever the operator chose, not "chela"."""
    _install(hooks.hooks_spec(PORT), marketplace="acme")
    copies = hooks.installed_plugins()
    assert copies[0].marketplace == "acme"


def test_marketplace_is_read_from_the_cache_scan_fallback(env):
    _install(hooks.hooks_spec(PORT), marketplace="acme", register=False)
    (hooks.plugins_dir() / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    copies = hooks.installed_plugins()
    assert copies[0].found_via == "a scan of the plugin cache"
    assert copies[0].marketplace == "acme"


# --- doctor -----------------------------------------------------------------------

def test_doctor_ERRORs_when_the_installed_manifest_disagrees(env):
    _render()
    _install(_stale())
    findings = _check()
    errors = _levels(findings, doctor.ERROR)
    assert errors, "a stale installed manifest is a DEAD feature, not a warning"
    body = _text(errors)
    assert "INSTALLED" in body
    assert "PermissionRequest" in body and "timeout" in body
    assert str(hooks.plugins_dir()) in body     # it NAMES the file it read
    assert "STARTUP" in body                    # a running fleet keeps the stale hooks
    assert "chela update" in body                # points at the automatic fix...
    # the sentence that CREDITS `chela update` with doing the refresh, not just the token
    # appearing somewhere — a bare "chela update" in body substring also passes if the
    # sentence instead says `chela update` does NOT help, run something else
    assert "chela update` already refreshes this copy for you" in body
    assert "non-interactively" in body            # ...the refresh needs no operator input...
    assert "no uninstall/reinstall needed" in body
    assert "/plugin uninstall" not in body        # ...not a manual uninstall/reinstall
    # this PR removed BOTH slash-command lines from the doctor Fix clause too — bar both,
    # not just the uninstall half, or the interactive reinstall this ticket exists to
    # remove can come straight back in the other line (mirrors the `chela plugin` test)
    assert "/plugin install" not in body
    # the two real CLI calls `chela update` actually runs — both verbs, not just one
    assert "claude plugin marketplace update <marketplace>" in body
    assert "claude plugin update chela@<marketplace>" in body
    # `_update_plugin` (chela/update.py) runs the marketplace refresh FIRST, then the
    # plugin update — reversed, the plugin update resolves against marketplace metadata
    # that hasn't been refreshed yet, so it's a no-op and the operator stays stale despite
    # following the instructions correctly. `in` membership is order-blind; pin the order.
    marketplace_pos = body.index("claude plugin marketplace update <marketplace>")
    plugin_pos = body.index("claude plugin update chela@<marketplace>")
    assert marketplace_pos < plugin_pos, (
        "marketplace update must be named FIRST — that's the order `_update_plugin` "
        "actually runs them in"
    )
    # `_plugin_marketplaces()` only ever returns CONFIRMED-installed copies (it skips
    # cache-scan-only hits) — the Fix clause must not promise a refresh scope the code
    # doesn't deliver.
    assert "confirmed-installed copy" in body
    # bans the ACTION (uninstall+reinstall the plugin by hand), not just one spelling of
    # it — "no uninstall/reinstall needed" is the only place those two words may appear
    without_credit = body.replace("no uninstall/reinstall needed", "")
    assert "uninstall" not in without_credit and "reinstall" not in without_credit, (
        "the Fix clause must never instruct an uninstall+reinstall, in ANY spelling — "
        "not only the /plugin slash-command one"
    )


def test_doctor_ERRORs_when_a_port_drift_reaches_the_installed_copy(env):
    """CMX-41's bug, now checked where it actually bites: the copy agents load."""
    _render(port=PORT)
    _install(hooks.hooks_spec(5001))
    body = _text(_levels(_check(PORT), doctor.ERROR))
    assert "5001" in body and "5005" in body


def test_doctor_ERRORs_when_the_installed_copy_cannot_be_found(env):
    _render()
    findings = _check()
    assert _levels(findings, doctor.ERROR), "no installed copy must never report green"
    assert "NOT INSTALLED" in _text(findings)


def test_doctor_ERRORs_when_the_installed_copy_cannot_be_read(env):
    """The cache may change shape between Claude Code releases. The failure mode when it
    does is a loud 'I cannot verify this' — a silent green here is the bug being fixed."""
    _render()
    root = _install(hooks.hooks_spec(PORT))
    (root / "hooks" / "hooks.json").write_text("{ truncated", encoding="utf-8")
    findings = _check()
    assert _levels(findings, doctor.ERROR)
    assert "cannot verify" in _text(findings)


def test_doctor_ERRORs_when_the_installed_manifest_has_no_hooks_object(env):
    _render()
    root = _install(hooks.hooks_spec(PORT))
    (root / "hooks" / "hooks.json").write_text('{"plugins": []}', encoding="utf-8")
    findings = _check()
    assert _levels(findings, doctor.ERROR)
    assert "cannot verify" in _text(findings)


def test_doctor_is_green_when_installed_and_rendered_agree(env):
    _render()
    _install(hooks.hooks_spec(PORT))
    findings = _check()
    assert not _levels(findings, doctor.ERROR)
    assert not _levels(findings, doctor.WARN)
    body = _text(findings)
    assert "installed plugin matches" in body
    assert str(hooks.plugins_dir()) in body      # and it says WHICH file it read


def test_doctor_ERRORs_when_the_rendered_manifest_is_stale(env):
    """The rendered copy is what a reinstall COPIES — stale here reinstalls stale."""
    directory = _render()
    hooks._write_json(directory / "hooks" / "hooks.json", _stale())
    _install(hooks.hooks_spec(PORT))
    body = _text(_levels(_check(), doctor.ERROR))
    assert "rendered plugin" in body and "STALE" in body


def test_doctor_says_nothing_when_no_plugin_was_ever_rendered(env):
    """`chela plugin` is step one. Not having run it is not a broken install."""
    assert _check() == []


# --- `chela plugin` closes the loop ------------------------------------------------

def test_chela_plugin_names_the_cache_path_when_the_install_is_stale(env, capsys):
    directory = _render()
    _install(_stale())
    main._report_installed_plugin(directory, PORT)
    out = capsys.readouterr().out
    assert "STALE INSTALL" in out
    assert str(hooks.installed_plugins()[0].manifest) in out
    assert "/plugin uninstall" not in out
    # this PR removed BOTH slash-command lines from the STALE-INSTALL branch — bar both,
    # not just the uninstall half, or the interactive reinstall this ticket exists to
    # remove can come straight back in the other line
    assert "/plugin install" not in out
    # the rationale the by-hand fallback exists for: chela never touches the cache itself
    assert "chela will not write into Claude Code's plugin cache directly" in out
    # the sentence that CREDITS `chela update` with doing the refresh, not just the line
    # an operator copy-pastes — round 5's mutation renamed this to `chela plugin` (the
    # command already running when this message prints, which does NOT refresh the cache)
    # and the tightened assertion below did not notice because it only pins the action line
    assert "chela update` already refreshes it" in out
    assert "\n    chela update\n" in out          # the action line itself, not just the prose
    assert "claude plugin marketplace update <marketplace>" in out   # the real CLI verb...
    assert "claude plugin update chela@<marketplace>" in out         # ...both of them
    # `_update_plugin` (chela/update.py) runs the marketplace refresh FIRST, then the
    # plugin update — reversed, the by-hand fallback resolves against stale marketplace
    # metadata and leaves the operator exactly as stale. `in` membership is order-blind.
    marketplace_pos = out.index("claude plugin marketplace update <marketplace>")
    plugin_pos = out.index("claude plugin update chela@<marketplace>")
    assert marketplace_pos < plugin_pos, (
        "marketplace update must be named FIRST — that's the order `_update_plugin` "
        "actually runs them in"
    )
    # the claim the verbs above are evidence FOR: no manual round-trip is needed
    assert "non-interactively" in out
    assert "no uninstall/reinstall needed" in out
    # bans the ACTION (uninstall+reinstall the plugin by hand), not just one spelling of
    # it — "no uninstall/reinstall needed" is the only place those two words may appear
    without_credit = out.replace("no uninstall/reinstall needed", "")
    assert "uninstall" not in without_credit and "reinstall" not in without_credit, (
        "the STALE-INSTALL block must never instruct an uninstall+reinstall, in ANY "
        "spelling — not only the /plugin slash-command one"
    )


def test_chela_plugin_says_so_when_nothing_is_installed(env, capsys):
    directory = _render()
    main._report_installed_plugin(directory, PORT)
    out = capsys.readouterr().out
    assert "cannot find an INSTALLED copy" in out
    assert "/plugin marketplace add" in out


# --- the recap hook: a NEW hook is the version trapdoor, one turn on ------------------
#
# The installed copy is a COPY, made at install time and keyed on the plugin version. Add a
# hook and every already-installed fleet keeps a manifest that simply does not have it —
# and the old drift check read only the FIRST hook of each event, so a SessionStart that
# still said `http` (the transport it never fires over) would have compared green while the
# recap reached nobody. That is CMX-56's bug with a new face; it is checked here.

def _http_session_start(port: int = PORT) -> dict:
    """The manifest as it was BEFORE the recap: SessionStart over http, which never fires."""
    spec = hooks.hooks_spec(port)
    spec["hooks"]["SessionStart"] = [{"hooks": [{
        "type": "http",
        "url": f"http://127.0.0.1:{port}/hooks/SessionStart",
        "timeout": 2,
    }]}]
    return spec


def test_drift_catches_an_installed_copy_with_no_recap_hook(env):
    drift = hooks.manifest_drift(_http_session_start(), hooks.hooks_spec(PORT))
    assert any("SessionStart" in d and "command" in d for d in drift)


def test_doctor_ERRORs_when_the_installed_copy_predates_the_recap(env):
    _render()
    _install(_http_session_start())
    body = _text(_levels(_check(), doctor.ERROR))
    assert "SessionStart" in body and "curl" in body


def test_drift_sees_a_hook_dropped_from_an_entry_that_declares_two(env):
    """The old check read entries[0]["hooks"][0] and nothing else."""
    expected = hooks.hooks_spec(PORT)
    expected["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "x",
                                                  "timeout": 1})
    drift = hooks.manifest_drift(hooks.hooks_spec(PORT), expected)
    assert any("Stop" in d and "1 hook(s)" in d for d in drift)


# --- CMX-321: installed and byte-identical is not the same claim as LOADS -------------

def test_registered_marketplaces_is_none_when_the_registry_is_missing(env):
    """No `known_marketplaces.json` at all is "cannot verify", never "empty registry" —
    a plugin can only ever be installed FROM a marketplace, so an unreadable registry is
    chela's blind spot, not proof every marketplace vanished."""
    assert hooks.registered_marketplaces() is None


def test_registered_marketplaces_reads_the_keys(env):
    _register_marketplaces("anthropic-agent-skills", "chela")
    assert hooks.registered_marketplaces() == {"anthropic-agent-skills", "chela"}


def test_registered_marketplaces_is_none_when_the_file_is_not_a_dict(env):
    """Present but malformed (a JSON list, here) must land on the same "cannot verify"
    branch as a missing file — never an empty set, which would read as EVERY installed
    copy's marketplace being gone (a false ERROR from doctor, a false ⛔ from `chela
    update`) on any machine where this registry happens to be malformed."""
    path = hooks.plugins_dir() / "known_marketplaces.json"
    path.write_text("[]", encoding="utf-8")
    assert hooks.registered_marketplaces() is None


def test_registered_marketplaces_is_none_when_the_json_is_unparseable(env):
    """The third of three documented reachability paths for "missing or unreadable":
    missing (tested above) and present-but-not-a-dict (tested above) both land on `None`,
    but so must present-and-not-even-JSON — the `ValueError` half of
    `except (OSError, ValueError)`. Getting this arm wrong is not quiet: an uncaught
    `json.JSONDecodeError` would propagate out of this function into `chela doctor`,
    `chela update` and `chela plugin`, the three surfaces that call it."""
    path = hooks.plugins_dir() / "known_marketplaces.json"
    path.write_text("not json", encoding="utf-8")
    assert hooks.registered_marketplaces() is None


def test_marketplace_missing_is_false_when_the_registry_cannot_be_read(env):
    """Never guess "gone" from an absent file — that would false-positive on every
    environment where this registry happens not to exist for unrelated reasons."""
    _install(hooks.hooks_spec(PORT))
    copy = hooks.installed_plugins()[0]
    assert hooks.marketplace_missing(copy) is False


def test_marketplace_missing_is_false_when_the_marketplace_is_present(env):
    _install(hooks.hooks_spec(PORT))
    _register_marketplaces("anthropic-agent-skills", "chela")
    copy = hooks.installed_plugins()[0]
    assert hooks.marketplace_missing(copy) is False


def test_marketplace_missing_is_true_when_the_registry_no_longer_has_it(env):
    """The exact shape found 2026-08-30: `installed_plugins.json` still lists the plugin,
    the cached manifest is perfectly readable, but Claude Code's marketplace registry no
    longer has the marketplace it was installed under."""
    _install(hooks.hooks_spec(PORT))
    _register_marketplaces("anthropic-agent-skills", "superpowers-marketplace")
    copy = hooks.installed_plugins()[0]
    assert hooks.marketplace_missing(copy) is True


def test_marketplace_missing_is_false_when_the_marketplace_could_not_be_determined(env):
    """A registry entry keyed with no `@marketplace` suffix at all leaves `marketplace`
    `None` — a different, already-handled gap than a confirmed-but-vanished marketplace,
    and not something this check should also flag."""
    root = _install(hooks.hooks_spec(PORT), register=False)
    registry = hooks.plugins_dir() / "installed_plugins.json"
    registry.write_text(json.dumps({"version": 2, "plugins": {
        "chela": [{"scope": "user", "installPath": str(root), "version": "0.1.0"}],
    }}), encoding="utf-8")
    _register_marketplaces("anthropic-agent-skills")   # a real registry — just no match
    copy = hooks.installed_plugins()[0]
    assert copy.marketplace is None
    assert hooks.marketplace_missing(copy) is False


def test_doctor_ERRORs_when_the_marketplace_is_gone(env):
    """This must read as a LOAD failure, not a staleness one — `claude plugin list` calls
    it "failed to load", and a manifest comparison alone can never see it."""
    _render()
    # "acme" (not "chela") on purpose — "chela" appears in this message for reasons that
    # have nothing to do with the slug (`chela@...`, "chela does not know..."), so pinning
    # the slug with the tool's own name can never fail when the slug is rendered blank.
    _install(hooks.hooks_spec(PORT), marketplace="acme")
    _register_marketplaces("anthropic-agent-skills")
    body = _text(_levels(_check(), doctor.ERROR))
    assert "GONE" in body
    assert "CANNOT LOAD IT AT ALL" in body
    # a bare "acme" also shows up in the installed copy's cache PATH regardless of whether
    # the slug is actually interpolated — pin the two spots that render `copy.marketplace`
    # through an f-string, which a blanked slug cannot satisfy.
    assert "marketplace 'acme' is GONE" in body   # names the vanished marketplace slug
    assert "chela@acme" in body
    assert "failed to load" in body
    assert "STALE" not in body                    # never conflated with the drift wording
    # a third f-string render site, in the fix instruction itself — a bare "claude plugin
    # marketplace add" substring survives a blanked slug just as easily as the two above did
    # before they were pinned (docs/defeat_shapes/79); pin the interpolated phrase instead.
    assert "path-or-url-to-the-acme-marketplace" in body
    # a fourth render site: the one piece of evidence in this message is WHICH registry
    # file the verdict was read from — a blanked path is indistinguishable from a correct
    # one to every assertion above (docs/defeat_shapes/79, at a fourth site).
    assert str(hooks.plugins_dir() / "known_marketplaces.json") in body


def test_doctor_reports_the_gone_marketplace_even_with_zero_manifest_drift(env):
    """The manifest can be byte-for-byte current and this must still fire — it is not a
    drift check at all."""
    _render()
    _install(hooks.hooks_spec(PORT))          # matches the rendered manifest exactly
    _register_marketplaces("anthropic-agent-skills")
    findings = _check()
    assert _levels(findings, doctor.ERROR)
    assert "installed plugin matches" not in _text(findings)


def test_doctor_reports_the_gone_marketplace_instead_of_stale_when_both_are_true(env):
    """A copy can be BOTH stale AND unloadable at once — report the load failure (the more
    severe, more specific claim) and skip the now-moot drift comparison, so an operator is
    never told to fix hook content that will not matter until the marketplace is back."""
    _render()
    _install(_stale())
    _register_marketplaces("anthropic-agent-skills")
    body = _text(_levels(_check(), doctor.ERROR))
    assert "CANNOT LOAD IT AT ALL" in body
    assert "THE HOOKS THAT RUN ARE STALE" not in body


def test_chela_plugin_names_a_gone_marketplace_distinctly_from_a_stale_install(env, capsys):
    """Mirrors the doctor-side guards (`test_doctor_ERRORs_when_the_marketplace_is_gone`,
    `test_doctor_reports_the_gone_marketplace_instead_of_stale_when_both_are_true`): a
    ZERO-drift install can never exercise the `continue` that pre-empts the drift report,
    and "chela" is not a safe marketplace name to pin a slug with — it appears in this
    message for reasons that have nothing to do with the slug (`chela@...`, "chela does
    not know where it came from"). Stale + "acme" makes both branches live at once, so a
    mutated `continue` (which lets the drift report ALSO fire) and a blanked slug (which
    "STALE" or "chela" alone can never catch) both go red.
    """
    directory = _render()
    root = _install(_stale(), marketplace="acme")
    _register_marketplaces("anthropic-agent-skills")
    main._report_installed_plugin(directory, PORT)
    out = capsys.readouterr().out
    assert "GONE" in out
    assert "will not load" in out.lower()
    assert "STALE INSTALL" not in out
    # the gone-marketplace branch must PRE-EMPT the drift report for the same copy — a
    # `continue` mutated away lets this line also print, since `_stale()` really does drift
    assert "agrees with what was just rendered" not in out
    assert "marketplace 'acme' is GONE" in out    # names the vanished marketplace slug
    assert "claude plugin marketplace add" in out
    # WHICH installed manifest will not load — a blanked path satisfies every assertion
    # above just as well as a correct one (docs/defeat_shapes/79, at a fourth site).
    assert str(root / "hooks" / "hooks.json") in out
