### Security

- **Settings, updates, schedules, dispatcher writes and terminal shares now need the operator's
  browser session.** Saving settings (including the agent permission mode), Update/Restart,
  adding, toggling or deleting a schedule, promoting a backlog item, initialising a repo,
  creating a Linear task, and starting or changing a terminal share used to act on any request
  that reached the dashboard over loopback, so a dispatched agent could do any of them with one
  HTTP call. They now need the same proof as an override decision: the session cookie, the
  page's token and an `Origin` naming the dashboard's own host and port. If any is missing or
  wrong, the dashboard returns 403 and does nothing. Reading settings still needs no proof, the
  dashboard's own buttons send the token, and `chela schedule` / `chela update` work from a
  terminal as before.
