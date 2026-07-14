"""THROWAWAY — a deliberately failing test, to watch CI actually go red (CMX-69).

This file exists only on the `cmx-69-ci-red-probe` branch. It is deleted with it.
"""


def test_this_fails_on_purpose():
    assert 1 == 2, "deliberate failure: proving the CI gate can be SEEN to go red"
