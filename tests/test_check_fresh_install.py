"""scripts/check_fresh_install.sh + `chela doctor`'s required/optional split — CMX-92.

The script runs the README install block verbatim on a clean container, so the block it
carries and the block the README shows must be the same text; and a first install has no
Telegram and no Linear, so their absence must never fail `chela doctor`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from chela import doctor, main, runtime_truth
from chela.runtime_truth import ERROR, OK, WARN, Fact, Finding, observed

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_fresh_install.sh"


def _script_block() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"# BEGIN INSTALL BLOCK\nINSTALL_BLOCK='(.*?)'\n# END INSTALL BLOCK\n",
                  text, re.S)
    assert m, "the script's INSTALL_BLOCK markers are missing"
    return m.group(1)


def _doc_block(doc: Path, heading: str) -> str:
    section = doc.read_text().split(heading, 1)[1]
    m = re.search(r"```bash\n(.*?)\n```", section, re.S)
    assert m, f"no bash block under {heading!r} in {doc.name}"
    return m.group(1)


@pytest.mark.parametrize("doc, heading", [
    (ROOT / "README.md", "\n## Install\n"),
    (ROOT / "docs" / "GETTING_STARTED.md", "\n## 1. Install\n"),
])
def test_the_script_runs_exactly_the_documented_install_block(doc, heading):
    assert _script_block() == _doc_block(doc, heading)


def test_the_block_names_the_url_the_script_swaps_for_the_local_commit():
    # If the README's URL changed and the script's did not, the swap would no-op and the
    # check would quietly test GitHub's main instead of the commit under review.
    assert 'GITHUB_URL="https://github.com/Devail1/chelamux"' in SCRIPT.read_text()
    assert "git+https://github.com/Devail1/chelamux" in _script_block()


def test_no_container_runtime_is_reported_as_skipped_not_passed(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname",):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    out = subprocess.run([shutil.which("bash"), str(SCRIPT)], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 77, out.stdout + out.stderr
    assert out.stdout.startswith("SKIP: no usable container runtime")
    assert "PASS" not in out.stdout


# --- CMX-444: phases, deadlines, the cached image — against a FAKE runtime ---------------
#
# The real container check is network-bound (apt, the uv installer, a git install), so it
# runs in CI's own `fresh-install` job, never inside `pytest -q`. Here a fake docker stands
# in: it records every argv and can hang or fail any one phase on request.

_FAKE_RUNTIME = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  image) [ "${FAKE_CACHED:-0}" = 1 ]; exit ;;
  build)
    df="$(cat)"
    case "$df" in
      *apt-get*) ph="apt" ;;
      *astral.sh/uv*) ph="uv installer" ;;
      *) echo "unknown dockerfile" >&2; exit 9 ;;
    esac
    printf 'dockerfile[%s]\n' "$ph" >> "$FAKE_LOG"
    if [ "${FAKE_HANG:-}" = "$ph" ]; then echo "Get:1 slow mirror"; exec sleep 300; fi
    if [ "${FAKE_FAIL:-}" = "$ph" ]; then echo "E: build broke" >&2; exit 3; fi
    exit 0 ;;
  rm|run) exit 0 ;;
  exec)
    cmd="$5"
    case "$cmd" in
      *"uv tool install"*)
        if [ "${FAKE_BLOCK_EXIT:-0}" = 1 ]; then echo "block blew up"; exit 2; fi
        echo "block running"; exec sleep 300 ;;
      *"command -v chela"*)
        if [ "${FAKE_PROBE_HANG:-0}" = 1 ]; then exec sleep 300; fi
        [ "${FAKE_HANG:-}" != "git install" ] && [ "${FAKE_BLOCK_EXIT:-0}" != 1 ]; exit ;;
      *api/agents*)
        if [ "${FAKE_HANG:-}" = "dashboard up" ]; then printf 000; else printf 200; fi ;;
      *"chela doctor"*)
        echo "required checks: all pass"; [ "${FAKE_DOCTOR_FAIL:-0}" != 1 ]; exit ;;
    esac ;;
esac
exit 0
"""

PHASES = ["apt", "uv installer", "git install", "dashboard up"]


@pytest.fixture
def fake(tmp_path):
    runtime = tmp_path / "fake-docker"
    runtime.write_text(_FAKE_RUNTIME)
    runtime.chmod(0o755)
    src = tmp_path / "src"
    src.mkdir()
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "f").write_text("x")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    log = tmp_path / "runtime.log"

    def run(**env):
        full = {**os.environ, "CHELA_CONTAINER_RUNTIME": str(runtime),
                "CHELA_FRESH_INSTALL_TIMEOUT": "2", "FAKE_LOG": str(log), **env}
        out = subprocess.run([str(SCRIPT), str(src)], env=full, capture_output=True,
                             text=True, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        return out, calls

    run.runtime = runtime
    return run


def _tag() -> str:
    return subprocess.run([str(SCRIPT), "--image-tag"], capture_output=True, text=True,
                          check=True, timeout=30).stdout.strip()


@pytest.mark.parametrize("phase", PHASES)
def test_a_hanging_phase_exits_75_and_names_itself(fake, phase):
    out, _ = fake(FAKE_HANG=phase)
    assert out.returncode == 75, out.stdout + out.stderr
    assert (f"TIMEOUT: phase '{phase}' did not complete within 2s — infra: did not "
            "complete (NOT a pass)") in out.stderr
    assert "PASS" not in out.stdout
    # The phases before the hung one finished and are timed; the hung one and later are not.
    done = PHASES[:PHASES.index(phase)]
    for p in PHASES:
        assert (f"phase {p}: " in out.stderr) is (p in done), (p, out.stderr)


def test_a_probe_that_never_returns_still_hits_the_phase_deadline(fake):
    out, _ = fake(FAKE_PROBE_HANG="1", CHELA_FRESH_INSTALL_PROBE_TIMEOUT="1")
    assert out.returncode == 75, out.stdout + out.stderr
    assert "TIMEOUT: phase 'git install' did not complete within 2s" in out.stderr


@pytest.mark.parametrize("phase", ["apt", "uv installer"])
def test_a_build_timeout_shows_the_builds_own_output(fake, phase):
    out, _ = fake(FAKE_HANG=phase)
    assert "Get:1 slow mirror" in out.stderr


def test_every_phase_is_timed_on_a_pass(fake):
    out, _ = fake()
    assert out.returncode == 0, out.stdout + out.stderr
    for p in PHASES:
        assert re.search(rf"^phase {p}: \d+s$", out.stdout, re.M), (p, out.stdout)
    assert f"prerequisites: built ({_tag()})" in out.stdout
    assert out.stdout.rstrip().splitlines()[-1] == (
        f"PASS: fresh install on {fake.runtime} ubuntu:24.04")


def test_the_two_builds_layer_uv_on_the_apt_image(fake):
    _, calls = fake()
    tag = _tag()
    digest = tag.split(":", 1)[1]
    builds = [c for c in calls if c.startswith("build ") or c.startswith("dockerfile[")]
    assert builds == [
        f"build -q -t chela-fresh-install-apt:{digest} -",
        "dockerfile[apt]",
        f"build -q -t {tag} --build-arg BASE=chela-fresh-install-apt:{digest} -",
        "dockerfile[uv installer]",
    ]


def test_a_cached_prereqs_image_skips_both_builds(fake):
    out, calls = fake(FAKE_CACHED="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"image inspect {_tag()}" in calls
    assert not [c for c in calls if c.startswith("build")]
    assert f"prerequisites: cached ({_tag()})" in out.stdout
    assert "phase apt:" not in out.stdout and "phase uv installer:" not in out.stdout


@pytest.mark.parametrize("phase", ["apt", "uv installer"])
def test_a_failing_build_is_a_failure_not_a_timeout(fake, phase):
    out, _ = fake(FAKE_FAIL=phase)
    assert out.returncode == 1, out.stdout + out.stderr
    assert f"FAIL: phase '{phase}' — the image build failed (exit 3)" in out.stderr
    assert "E: build broke" in out.stderr
    assert "TIMEOUT" not in out.stderr


def test_an_install_block_that_dies_is_a_failure_not_a_timeout(fake):
    out, _ = fake(FAKE_BLOCK_EXIT="1")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "FAIL: the install block exited during phase 'git install'" in out.stderr
    assert "block blew up" in out.stderr
    assert "TIMEOUT" not in out.stderr


def test_a_failing_doctor_fails_the_check(fake):
    out, _ = fake(FAKE_DOCTOR_FAIL="1")
    assert out.returncode == 1
    assert "FAIL: chela doctor reported a required check failing" in out.stderr
    assert "PASS" not in out.stdout


def test_the_image_tag_is_a_hash_of_the_dockerfiles(tmp_path):
    tag = _tag()
    assert re.fullmatch(r"chela-fresh-install-prereqs:[0-9a-f]{12}", tag), tag
    edited = tmp_path / "check.sh"
    edited.write_text(SCRIPT.read_text().replace("git tmux curl", "git tmux curl jq"))
    other = subprocess.run(["bash", str(edited), "--image-tag"], capture_output=True,
                           text=True, check=True, timeout=30).stdout.strip()
    assert other != tag and other.startswith("chela-fresh-install-prereqs:")


# --- the CI job's retry step, EXECUTED against a scripted check ------------------------

def _retry_step() -> str:
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    [step] = [s for s in wf["jobs"]["fresh-install"]["steps"]
              if s.get("name") == "Fresh install (one retry on an infra timeout)"]
    return step["run"]


@pytest.mark.parametrize("codes, rc, calls, says", [
    ([0], 0, 1, None),
    ([75, 0], 0, 2, "::warning title=infra: did not complete::attempt 1"),
    ([75, 75], 1, 2, "::error title=infra: did not complete::the fresh-install check "
                     "timed out twice — not a pass"),
    ([75, 1], 1, 2, "::warning title=infra: did not complete::attempt 1"),
    ([1], 1, 1, None),
    ([2], 2, 1, None),
    ([77], 1, 1, "::error title=fresh install did NOT run::"),
])
def test_the_ci_step_retries_only_an_infra_timeout(tmp_path, codes, rc, calls, says):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    counter = tmp_path / "n"
    counter.write_text("0")
    codes_s = " ".join(map(str, codes))
    check = scripts / "check_fresh_install.sh"
    check.write_text(f"""#!/usr/bin/env bash
n=$(( $(cat {counter}) + 1 )); echo $n > {counter}
codes=({codes_s}); exit ${{codes[$((n-1))]}}
""")
    check.chmod(0o755)
    # GitHub runs `run:` as `bash --noprofile --norc -eo pipefail {0}`.
    script = tmp_path / "step.sh"
    script.write_text(_retry_step())
    out = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert out.returncode == rc, out.stdout + out.stderr
    assert counter.read_text().strip() == str(calls)
    if says:
        assert says in out.stdout


# --- chela doctor: required vs optional ------------------------------------------------

def test_only_telegram_and_linear_are_optional():
    optional = sorted(f.name for f in runtime_truth.facts() if not f.required)
    assert optional == ["telegram.sender_allowlist", "tracker.linear_issue_cap"]


@pytest.mark.parametrize("required", [True, False])
def test_audit_stamps_the_facts_required_flag_on_its_findings(required):
    f = Fact(name="x", declared_by="d", owned_by="o", declare=lambda: None,
             read_back=lambda: observed(1), required=required,
             report=lambda _d, _o: [Finding(ERROR, "broken")])
    [finding] = runtime_truth.audit(f)
    assert finding.required is required
    expect = "✗ broken" if required else "✗ (optional) broken"
    assert finding.render() == expect


def test_an_ok_optional_finding_carries_no_optional_marker():
    assert Finding(OK, "fine", required=False).render() == "✓ fine"
    assert Finding(WARN, "hm", required=False).render() == "! (optional) hm"


@pytest.mark.parametrize("required, exits", [(True, True), (False, False)])
def test_doctor_exits_1_only_on_a_required_error(monkeypatch, capsys, required, exits):
    monkeypatch.setattr(doctor, "check", lambda: [
        Finding(OK, "fine"), Finding(ERROR, "broken", required=required)])
    if exits:
        with pytest.raises(SystemExit) as e:
            main.cmd_doctor(None)
        assert e.value.code == 1
        out = capsys.readouterr().out
        assert "1 problem(s) — see above." in out
        assert "required checks: all pass" not in out
    else:
        main.cmd_doctor(None)
        out = capsys.readouterr().out
        assert "1 optional problem(s) — not needed for a first run." in out
        assert out.rstrip().endswith("required checks: all pass")


def test_no_linear_tracker_reads_as_not_configured_not_as_nothing():
    assert runtime_truth._linear_issue_cap_report({}, observed({})) == [
        Finding(OK, "Linear tracker: not configured (optional)")]
