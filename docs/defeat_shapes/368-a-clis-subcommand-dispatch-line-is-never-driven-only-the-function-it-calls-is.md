## 368. A CLI subcommand's `elif args.command == "..."` dispatch line is never driven — every test calls the handler function directly, skipping argparse entirely

**Assertion form:** a new CLI subcommand (`chela request-push`) gets a thorough test suite for
the function it eventually calls (`dispatcher.request_push`) and for the daemon-side function
that consumes what it wrote (`dispatcher._apply_push_request`, `dispatcher.tick`) — argument
validation, error paths, marker contents, all real, all green. But every single one of those
tests calls `dispatcher.request_push(...)` (or a lower function) in Python, directly, by name.
Nothing in the suite ever builds `sys.argv = ["chela", "request-push", ...]` and calls
`main.main()`. The subcommand's own `main.py` dispatch line —
`elif args.command == "request-push": cmd_request_push(args)` — and `cmd_request_push` itself
(flag parsing, `--pr-body-file`/stdin handling, the error-path `sys.exit(1)`) are therefore
covered by *nothing*, even though `cmd_request_push` is the ONLY way the feature is reachable
in production; a dispatched agent never imports `chela.dispatcher` and calls a Python function,
it types a shell command.

**Mutation that defeats it:** dead-code the dispatch line so the subcommand is unreachable:

```diff
-     elif args.command == "request-push":
+     elif False and args.command == "request-push":
          cmd_request_push(args)
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stays green (4029 passed, 0 failed) — every test
that exercises the feature's behavior does so by calling `dispatcher.request_push` straight,
never through the parser, so none of them can tell the subcommand itself was unplugged. In
production, `chela request-push <id>` would now do nothing at all: no marker, no error, no
exit code — the process would fall through `main()`'s `if/elif` chain with no branch taken and
return normally, silently, as if the command had succeeded.

**Why this is distinct from [[338|shape 338]]:** 338 is a production call site that passes one
argument among several, proven only by unit tests of the class it constructs — the callee is
driven directly with all the OTHER arguments correct, and only one kwarg goes unchecked at the
seam. Here there is no seam to check an argument at; the entire CLI entry point — parser
registration through handler dispatch — has zero test coverage of its own, only of the pure
function underneath it. It is also distinct from [[07|shape 7]] ("two callers, one guarded"):
this is not N call sites with one left undriven, it is the single production call site
(argparse dispatch) versus the N *test* call sites (direct function calls) that all skip it.

**Guard form that survives:** for any new CLI subcommand, add at least one test that builds a
real `sys.argv` for it and calls the real `main.main()` — mocking only the function the handler
delegates to (to pin exact forwarding: positional vs. keyword, defaults) — AND at least one
true end-to-end test with nothing mocked below the dispatch, asserting the real side effect
(here: the marker file lands on disk) happened. Mirrors the pattern
`tests/test_dispatcher_task_finished.py`'s own "end-to-end: cmd_task_finished driving the REAL
dispatcher.verify_self_check" section already used for the sibling `chela task-finished` hop —
this shape is what happens when a sibling CLI command ships without that section ever getting
written for it.

**Found:** `chela judge`, PR #512 (issue #502 B2, rework round 1). `chela/main.py`'s
`cmd_request_push` and its `elif args.command == "request-push":` dispatch line had no test
anywhere in `tests/` driving either the parser or the handler — every test in
`tests/test_dispatcher_push_request.py` called `dispatcher.request_push`/
`dispatcher._apply_push_request` directly. The judge mutated the dispatch line to
`elif False and args.command == "request-push":` in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4029 passed, 0 failed, 0 errors) with
the corruption in place. Closed by a "CLI: `cmd_request_push` parser wiring" section added to
`tests/test_dispatcher_push_request.py`: an end-to-end test driving `main.main()` with a real
`sys.argv` and a real run row, asserting the marker file the real `dispatcher.request_push`
writes actually lands on disk, plus mocked-forwarding tests pinning the exact
`task_id`/`pr_title`/`pr_body` arguments (including `--pr-body-file`/stdin handling) and the
`sys.exit(1)` error path.
