### Added

- **The dashboard shows which Claude account the CLI is signed in to.** A line under the
  sidebar head reads the account email (`name@… · Org` when there is an org); hover shows the
  plan, the login method and when the login expires. It comes from `claude auth status --json`,
  read every 5 minutes and on dashboard load, and cached. "Claude not logged in" and a login
  that expires within 3 days (`CHELA_CLAUDE_LOGIN_WARN_DAYS`) show as warnings, and a CLI that
  is missing or fails reads "account: unknown", never a blank. (CMX-395)
- **A session started before an account switch says it may still use the old account.** chela
  records each change of the signed-in account it sees; a session that started before the
  latest one gets a muted badge with the old email on its sidebar row and pane header ("may
  still use", since a running session's account is not knowable). (CMX-395)
