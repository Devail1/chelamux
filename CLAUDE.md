# chelamux agent instructions

## Frontend

- `docs/FRONTEND.md` is the authority on chela's frontend (a Vite single-page app over a Flask
  API). When it conflicts with a generic skill, including the vendored React skills in
  `.claude/skills/` (`react-best-practices`, `composition-patterns`), `docs/FRONTEND.md` wins.
- Those skills come from Vercel and assume Next.js in places. Each `SKILL.md` opens with a
  `chela:scope` note saying which rules do not apply here; see `.claude/skills/VENDORED.md`.
