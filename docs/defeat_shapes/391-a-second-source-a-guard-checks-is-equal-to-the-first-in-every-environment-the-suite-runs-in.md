## 391. A second source a guard checks is equal to the first in every environment the suite runs in

**Assertion form:** a guard checks the same fact through two sources, because in production
they can disagree. CMX-391's conftest refuses a `CHELA_DIR` that names the operator's real
`~/.chela`. It reads both the env var **and** `config.CHELA_DIR`, and it computes "the
operator's home" from both `Path.home()` **and** the passwd entry. The tests call the refusal
helper directly, and they run a child suite with `CHELA_DIR` set to the real dir. Both prove
the check fires.

**Mutation that defeats it:** remove the second source. Pass `None` instead of
`config.CHELA_DIR`, or compute the home from `Path.home()` twice. The suite stays green. In
every environment it runs in, the per-test fixture sets the env and the attribute together,
and `HOME` equals the passwd home. The first source alone already produces the answer each
test expects, so the second is never the one that decides it. That is also how the redundant
arm can quietly disappear in a refactor.

**Guard form that survives:** build a fixture that holds the two sources **apart**, so that
the second one alone has to carry the verdict. Move only `config.CHELA_DIR` to the real dir
(the env stays scratch) and assert a refusal, once through the helper and once end to end
through a child suite whose per-test isolation is overridden. Run a child suite with `HOME`
borrowed and `CHELA_DIR` set to the **passwd** home's `.chela`. A related shape is
[[02|a fixture parked on a default]]. The difference here is that the environment is parked,
not a fixture value: nothing in the ordinary suite can make the two sources differ.

**Found:** CMX-391 rework round 1 (2026-09-29), PR #544. The judge mutated
`_refuse_real_chela_dir(config.CHELA_DIR)` → `(None)` and `pwd…pw_dir` → `str(Path.home())`
in `tests/conftest.py`. The suite stayed green (4307 passed). Closed in
`tests/test_isolation.py` by `test_the_per_test_check_refuses_a_real_config_chela_dir_on_its_own`,
`test_a_test_whose_config_chela_dir_is_the_real_one_is_refused_at_setup` and the
`borrowed` arm of `test_a_suite_pointed_at_the_real_dir_refuses_to_run`.
