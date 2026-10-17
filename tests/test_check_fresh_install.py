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


@pytest.mark.skipif(os.environ.get("CHELA_FRESH_INSTALL_CHECK") == "0",
                    reason="CHELA_FRESH_INSTALL_CHECK=0 turned the container check off")
def test_the_fresh_install_check_passes_in_a_container():
    """Runs wherever docker or podman answers (CI's runner has docker). Without one the
    script exits 77 and this test SKIPS with the script's own reason — never a pass."""
    out = subprocess.run([str(SCRIPT)], capture_output=True, text=True, timeout=900)
    if out.returncode == 77:
        pytest.skip(out.stdout.strip())
    assert out.returncode == 0, out.stdout + out.stderr
    assert "required checks: all pass" in out.stdout


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
