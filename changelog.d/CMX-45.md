### Security

- **A dispatched agent inherits an environment allowlist, not the daemon's env.** Coding,
  rework and judge agents now launch under `env -i` with only the names in
  `chela.envutil.AGENT_ENV_ALLOW` (plus the forwarded Claude/GitHub credentials and any
  `CHELA_CHILD_ENV_FORWARD` names) passed through. It replaces CMX-115's single-name strip
  (`unset CHELA_NOTIFY_URL`), which let every other daemon var through — including ~40 pm2
  bookkeeping vars and names no shell `unset` can remove. Need another var in agents
  (a proxy, `SSH_AUTH_SOCK`)? Add it to `CHELA_CHILD_ENV_FORWARD`. See
  `docs/TRUST_AND_APPROVAL_POSTURE.md` §2. (CMX-45)
