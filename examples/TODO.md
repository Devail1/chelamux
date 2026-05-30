# TODO

Each unchecked `- [ ]` bullet is a work item the dispatcher can pick up. It
spawns one agent in a git worktree per item; the agent opens a PR and strikes
its line. A `<!-- blocked: ... -->` marker makes the dispatcher skip a line.

## Open

- [ ] Add a `--version` flag to the CLI
- [ ] Write a docstring for the public API entry point
- [ ] Add a unit test for the config loader <!-- blocked: waiting on fixtures -->
