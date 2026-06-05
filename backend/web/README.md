# web — operator dashboard (React + Vite + TypeScript)

The browser UI for the BFF (`backend/src/frontend`). A small single-page app: a nav shell
and one page per concern (Home, Health, Surfaces, Risk, Run, Config). Every page loads from
the BFF through one typed `useFetch` hook and renders the three async states — loading,
typed error, and data — uniformly via `AsyncBlock`.

## TL;DR

```bash
cd backend/web
npm install
npm run dev      # http://localhost:5173, proxies /api and /healthz to the BFF on :8000
```

Start the BFF too (`cd backend && PYTHONPATH=src uv run python -m frontend`), open Run,
launch the `SAMPLE` provider, then look at Surfaces and Health.

## Scripts

| Command | What it does |
|---------|--------------|
| `npm run dev` | Vite dev server with an `/api` proxy to `http://localhost:8000` |
| `npm run build` | Type-check (`tsc`) then bundle (`vite build`) |
| `npm run test` | Component tests (Vitest + Testing Library, jsdom) |
| `npm run lint` | ESLint (flat config, typescript-eslint + react-hooks) |
| `npm run preview` | Serve the production build locally |

## Layout

- `src/api.ts` — the typed HTTP client; the response interfaces mirror the BFF's
  `serializers.py`. This is the seam — keep them in sync when a serializer changes.
- `src/hooks/useFetch.ts` — load JSON, expose `{data, error, loading}`.
- `src/components/` — `AppLayout` (nav + outlet), `AsyncBlock` (the three-state renderer).
- `src/pages/` — one folder-free page per route, each with a co-located `*.test.tsx`.

## Tests

The component tests stub `fetch` with hand-built fixtures (`src/test/fixtures.ts`) and
assert what the operator sees: the Surfaces page renders one row per fitted slice with the
SVI parameters, Risk renders the net Greeks per group, Health shows the four flags and the
backlog — plus the loading and error states, not just the happy path.

## Production origin

In production the web app and API need not share a port; set `FRONTEND_BASE_URL` on the
BFF to this app's origin (CORS) and serve `dist/` behind your reverse proxy.
