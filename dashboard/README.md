# MobiSentra Dashboard

Operator control center for MobiSentra AI — live camera grid, incident
feed with ack/escalate, evidence replay, filterable history. React +
Vite + TypeScript (strict) + Tailwind + socket.io-client. Phase 9
builds this screen-by-screen (`Doc/Implementation/phase-9-plan.md`).

## Run

```bash
pnpm dev          # http://localhost:5173 (shell renders without the backend)
```

The Vite dev server proxies `/api` and `/socket.io` (ws) to the backend
on `localhost:3000` — same-origin, no CORS anywhere in dev. Start the
backend (`../backend`) for live data; without it the shell still renders
(empty vehicle selector, status reflects the socket's truth).

## Test / verify

```bash
pnpm typecheck    # strict tsc
pnpm test         # vitest — no stack needed (ws tests spin an in-process server)
```

## Backend contract (source of truth: backend/src)

- REST under `/api`: incidents (list/detail, ack/escalate), cameras
  (registry + online), events (cursor paging), evidence clips.
  Types mirrored in `src/domain.ts`.
- Socket.IO: emit `subscribe (vehicleId, ack)` — one call joins
  `alerts:{vehicle}` (push `"event"`, EventRecord) and
  `cameras:{vehicle}` (push `"state"`, CameraState). The client
  (`src/ws/client.ts`) re-subscribes with a fresh ack after every
  reconnect — the silent-dead-feed trap, covered by a unit test.

## Layout

```
src/App.tsx        app shell (header: status + vehicle selector, 4 regions)
src/api/client.ts  typed REST client (/api base)
src/ws/client.ts   typed Socket.IO client (reconnect + re-subscribe barrier)
src/domain.ts      wire types mirrored from the backend
```
