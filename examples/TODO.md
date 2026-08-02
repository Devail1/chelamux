# TODO

Each unchecked `- [ ]` bullet is a work item the dispatcher can pick up. It
spawns one agent in a git worktree per item; the agent opens a PR and strikes
its line. A `<!-- blocked: ... -->` marker makes the dispatcher skip a line. A
`<!-- depends: "other task title" -->` marker (titles `;`-separated for more
than one) holds a line back from being CLAIMED — not hidden, just not yet
takeable — until every task it names has been struck `- [x]`.

## Open

- [ ] Add a `--version` flag to the CLI
- [ ] Write a docstring for the public API entry point
- [ ] Add a unit test for the config loader <!-- blocked: waiting on fixtures -->
- [ ] Publish the `--version` flag in the README <!-- depends: "Add a `--version` flag to the CLI" -->
