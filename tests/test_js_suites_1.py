"""CMX-487: shard 1 of the ``*.test.mjs`` suites. See :mod:`test_js_suites`."""
from test_js_suites import run_js_suite, shard_params


@shard_params(1)
def test_js_suite(suite):
    run_js_suite(suite)
