# Vendored skills

The two skills in this directory are copied from Vercel's
[`vercel-labs/agent-skills`](https://github.com/vercel-labs/agent-skills) so that every agent
working in a chelamux checkout (including a dispatched agent in its worktree) loads them for
React work. They are MIT-licensed: see `LICENSE-vercel-agent-skills` here and the Vercel block
in the repo's `NOTICE`.

| | |
|---|---|
| Source | https://github.com/vercel-labs/agent-skills |
| Commit | `063bee94c3f4df8453406c830b0a7df0f2860278` |
| Commit date | 2026-08-28 (`main` HEAD when vendored) |
| Vendored on | 2026-10-17 (CMX-179) |
| Licence | MIT (`license: MIT` in each `SKILL.md`; the upstream README says "MIT") |

| Here | Upstream path |
|---|---|
| `react-best-practices/` | `skills/react-best-practices/` |
| `composition-patterns/` | `skills/composition-patterns/` |

## What was copied, and what was left out

Copied byte for byte: each `SKILL.md` and every `rules/*.md` file, with the exceptions below.

Left out:

- `react-best-practices/rules/rendering-svg-precision.md`: it tells the agent to run
  `npx svgo`, which downloads and executes a package. Nothing vendored here may run a command,
  fetch remote content or ask for a tool.
- `AGENTS.md` in both skills: a generated concatenation of the rule files (and so it would
  carry the `npx svgo` line too). Reading `rules/` gives the same content.
- `README.md` (maintainer notes that run `pnpm install` / `pnpm build`), `metadata.json`
  (build input) and `rules/_template.md` (an authoring stub).

Every file that was copied was read before it was committed. None of them carries an
`allowed-tools` field, a script, a shell block or an instruction to fetch anything; the
remaining URLs are documentation links in "Reference:" lines and example strings inside code
samples.

## Local changes

One only: a block between `<!-- chela:scope begin -->` and `<!-- chela:scope end -->` right
after the frontmatter of each `SKILL.md`. It scopes the skill to chela's Vite SPA (no Next.js,
no server components, no API routes) and says `docs/FRONTEND.md` wins on any conflict.
Vercel's rule text is unchanged.

## Updating

1. Clone upstream and pick the new commit: `git rev-parse HEAD` gives the full 40-character SHA.
2. Read the diff since the commit above
   (`git diff 063bee94c3f4df8453406c830b0a7df0f2860278..HEAD -- skills/react-best-practices skills/composition-patterns`)
   and every new file in full. Leave out anything that runs a command, fetches remote content
   or asks for a tool, and list it above.
3. Copy the files over, then put each `chela:scope` block back and check that its list of rules
   still matches the upstream rule names.
4. Update the commit, both dates and the left-out list here, and check `NOTICE` still matches
   upstream's licence.
5. `uv run pytest -q tests/test_vendored_skills.py`.
