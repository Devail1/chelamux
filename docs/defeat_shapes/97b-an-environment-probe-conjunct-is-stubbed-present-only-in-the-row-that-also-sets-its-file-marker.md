## 97b. An environment-probe conjunct is stubbed present only in the row that also sets its file marker

**Assertion form:** `detect_test_cmd()` picks `uv run pytest` only when BOTH a file marker
(`uv.lock`) is in the repo AND an environment probe (`shutil.which("uv")`) finds the binary.
The test was a parametrized table where each row lists the files to write and the set of
binaries the `which` stub reports. The only row with `uv.lock` also listed `"uv"`, and no row
listed `"uv"` without `uv.lock` or `uv.lock` without `"uv"`. Inside the table, the two
conditions always moved together.

**Mutation that defeats it:** `if (worktree / "uv.lock").is_file() and shutil.which("uv"):` →
`if (worktree / "uv.lock").is_file():`. The suite stayed green (7759/7759): no row ever had a
lockfile with no uv binary, which is the real-world case the probe exists for (a CI runner
that has never installed uv). Dropping the file half would have survived for the same reason.

**Why it slips past review:** the `which` stub *looks* like it controls the environment
axis, because it is a column in the table. A column that only ever holds values matching
another column is not an independent axis. The table reads as a matrix, but it is really a
list of happy paths (see [[348|entry 348]] for the general N-conjunct rule and
[[342|entry 342]] for quantities that coincide in every fixture).

**Guard form that survives:** for every condition that mixes the filesystem with the
environment, give each conjunct its own row with the other conjunct held at the passing
value: marker without binary, binary without marker, and a marker present as a directory
rather than a file. Then pin precedence with rows where every marker and every binary is
present, so that reordering the branches also goes red. Check the result by flipping each
condition by hand, one at a time (`and` dropped, `is_file` → `exists`, tuple reordered,
glob made non-recursive). A row that never fails under any of those flips can be removed
from the table without losing any coverage.
