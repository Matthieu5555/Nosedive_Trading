# frontend

JS/Vite app for the workspace.

## TL;DR

**Not scaffolded yet** — this directory is empty (no `package.json`). When it's
created, this README documents how to run and verify it.

## Run (intended, once scaffolded)

```
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

## Verify (intended, once scaffolded)

```
npm run lint
npm test
```

## Conventions

Follows `/srv/project/.agent/conventions.md`. For UI work, test user-visible
behavior (query by role/label/text, not CSS selectors), cover empty/loading/error
states, and wait for real conditions rather than fixed timeouts. See the
`write-tests` skill. Keep this README current when you scaffold or change the app.
