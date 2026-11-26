### Fixed

- **Flaky `test_unread_service_freshness_refuses_instead_of_saying_up_to_date`.** The test left
  `commits_behind` unstubbed, so `/api/update/apply` compared the real checkout with its upstream
  ref. When a CI checkout was one commit behind, the route took the pull path and answered 200
  instead of refusing with 503. The test now stubs "nothing to pull". An autouse fixture in
  `tests/test_update_apply_route.py` now fails any test there that runs real git against the
  checkout. (CMX-509)
