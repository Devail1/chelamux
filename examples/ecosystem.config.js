// PM2 template for a chela fleet.
//
//   cp examples/ecosystem.config.js ~/.chela/ecosystem.config.js
//   cp examples/chela.env           ~/.chela/chela.env          # <- config goes HERE
//   pm2 start ~/.chela/ecosystem.config.js && pm2 save
//
// ⚠️ NOTICE WHAT IS MISSING: there is no `env:` block. That is the point. Every app
// starts through scripts/run-chela.sh, which sources ~/.chela/chela.env — so config
// lives in exactly one file and a `pm2 start` from a cold machine brings the fleet up
// with the same values a `chela` in your shell would use.
//
// The alternative (an `env:` per app) is what chela shipped before, and all three copies
// of CHELA_TMUX_SESSION still named the OLD tmux session a day after it was renamed —
// invisible, because the running processes had been fixed by hand and only a clean
// restart would have used the file. `chela doctor` now catches that.
//
// MIGRATION from an ecosystem file that HAD `env:` blocks: `pm2 restart --update-env`
// MERGES the environment — it will not remove a variable you deleted from the file. To
// actually clear one you need `pm2 delete <app>` and a fresh `pm2 start`, and it must
// come from a shell that is NOT inside tmux (see run-chela.sh on the TMUX_PANE leak).

const HOME = process.env.HOME;
// Point this at your checkout (or drop the path if `chela` and the scripts are on PATH).
const CHELA_REPO = `${HOME}/projects/chelamux`;
const RUN = `${CHELA_REPO}/scripts/run-chela.sh`;

const app = (name, args) => ({
  name,
  script: RUN,
  interpreter: 'bash',
  args,
  autorestart: true,
  max_restarts: 10,
  // No `env:` — chela.env is the source of truth. See the header.
});

module.exports = {
  apps: [
    app('chela-daemon', 'run'),            // scheduler + dispatcher + notifier
    app('chela-dashboard', 'dashboard'),   // the web dashboard (binds CHELA_DASHBOARD_PORT)
    app('chela-telegram', 'telegram'),     // the Telegram bridge (needs ~/.chela/secrets.env)
    {
      name: 'chela-agent-terminals',       // the ttyd wall: a script, not the CLI
      script: `${CHELA_REPO}/scripts/agent-terminals.sh`,
      interpreter: 'bash',
      autorestart: true,
    },
  ],
};
