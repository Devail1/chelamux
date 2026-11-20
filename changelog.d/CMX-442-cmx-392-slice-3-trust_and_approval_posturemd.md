### Security

- **The trust posture now says what actually gates a dashboard decision.**
  `docs/TRUST_AND_APPROVAL_POSTURE.md` §3 documents the browser-session proof (session
  cookie, page token, exact Origin) that an override, a share-request decision and the
  card's Merge must carry, and what it does not stop: a process running as the operator
  can still impersonate the browser on purpose. The merge-gate hook is documented as a
  speed bump that string-matches `curl`/`wget` argv, never the gate. The section ends with
  an audit of every mutating dashboard route: which require the proof, which should get it
  next, which add no power an agent lacks, and which agents post by design. Four new
  defeat shapes (`docs/defeat_shapes/392*.md`) record the test gaps found on the way.
  (CMX-442)
