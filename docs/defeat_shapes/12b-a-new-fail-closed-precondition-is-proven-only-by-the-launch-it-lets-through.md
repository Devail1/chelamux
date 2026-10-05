## 12b. A new fail-closed precondition is proven only by the launch it lets through

**Assertion form:** a launcher gains a new "make X or refuse to start" step (create a
directory, refuse if it is a symlink, `return 1` if it can't). The test drives the launcher
end-to-end on a clean host and asserts X now exists and is wired in (the dir is there, the
guest's argv mounts it). Every assertion lives on the success path.

**Mutation that defeats it:** neuter either refusal — `return 1` → `pass` after the failed
create, or `if p.is_symlink():` → `if False and p.is_symlink():`. On a clean `tmp_path` the
create never fails and nothing on the path is a symlink, so neither branch is ever entered and
the suite stays green while the launcher would now start a guest on a mount chela never vetted.

**Guard form that survives:** arm each refusal with a REAL failure of the kind it exists for —
not a stubbed raise of the helper — and assert the *absence of the launch*, not just the exit
code: the root is a regular file ⇒ `run()` returns non-zero AND no docker step / guest argv
was recorded; a symlink at each hop of the path (leaf, per-workspace dir) pointing at another
workspace's transcripts ⇒ the helper raises. Pair each with a negative control on the same
stubs (a clean host DOES launch; a real dir IS accepted) so a stub that can never launch
can't pass for a refusal.

**Found:** CMX-12 (`chela/share_sandbox.py` `run()` / `ensure_transcripts_dir`), guarded in
`tests/test_share_transcripts.py::test_the_launcher_refuses_to_start_when_the_dir_cannot_be_made`
and `::test_a_symlinked_transcripts_path_is_refused`.
