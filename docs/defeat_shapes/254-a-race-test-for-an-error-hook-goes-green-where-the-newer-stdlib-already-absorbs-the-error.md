## 254. A race test for an error hook goes green where the newer stdlib already absorbs the error

**Assertion form:** a fix passes a stdlib call an error hook. Here `shutil.rmtree(root,
onexc=_already_gone)` tolerates a `.git/maintenance.lock` that a detached `git maintenance`
child deletes while rmtree is walking the tree. The guard reproduces the race for real: it
monkeypatches `os.unlink` so the lock is gone by the time rmtree unlinks it, then asserts the
rebuild finishes. It went red on 3.11 without the hook, so it reads as covered.

**Mutation that defeats it:** remove the hook, `shutil.rmtree(root, **{...})` →
`shutil.rmtree(root)`. On 3.13 it stays green, because 3.13's `_rmtree_safe_fd_step` has its
own `except FileNotFoundError: continue` around the unlink. The ENOENT never reaches any
hook, so the race test can't tell the hook from no hook. The bug only exists on 3.11, and the
judge's checkout ran a newer interpreter. This is a sibling of [[56|shape 56]]. There the
fixture never reaches its branch on some interpreters. Here the fixture fires every time, but
the newer stdlib handles the failure before the code under test gets a turn.

**Why it looks covered:** the test does fail on the interpreter that showed the bug (3.11 CI),
and "reproduce the real race" sounds like the strongest guard possible. But it guards the
hook only where the stdlib leaves the error for the hook to handle, and nothing pins which
interpreter the suite runs on.

**Guard form that survives:** keep the real race test, and add one that pins the *contract*
on every interpreter. Stand in an `rmtree` that behaves like 3.11's: it really deletes the
tree, then reports a vanished entry to `onexc`/`onerror`, or raises `FileNotFoundError` when
no hook was given. Assert the rebuild finishes and that the hook went in under the keyword
right for this interpreter (`onexc` on 3.12+, `onerror` before). That makes the guard fail on
the absence of the hook, not on how the running interpreter happens to treat the failure.

**Found:** CMX-254 judge round 1, PR #767. Fixed in `tests/test_demo_gate.py`
(`test_make_repo_hands_rmtree_the_vanished_entry_hook`).
