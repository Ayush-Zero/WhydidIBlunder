# Ownership and abuse model (Phase 2 C8, Phase 3 D5 addendum)

**Status:** accepted for v1 on 2026-10-29; Phase 3 addendum on 2026-10-31.
**Scope:** the production API and worker (`server/`, `analyzer/`) and the Phase 3
browser contract (`web/`); there are no accounts.
**Related:** [`docs/api.md`](api.md), [`docs/storage.md`](storage.md),
[`docs/safety-review.md`](safety-review.md),
[`docs/testing.md`](testing.md), and `server/ratelimit.py`.

## The decision

WhydidIBlunder v1 has **no accounts and no authorization layer**. It is a
portfolio-scoped, public analysis tool: enter a Chess.com username, analyse a
public game, read the report. The ownership model is deliberately small:

1. **Unguessable job IDs** protect job control (poll and cancel).
2. **Per-IP rate limits** protect the expensive analysis and try-again endpoints.
3. **Public Chess.com data** is the only game data the API exposes.
4. **No secrets go into IDs.** An ID is an identifier, never a credential.
5. **Try-again is bounded by one engine slot and a hard timeout**, so the API
   cannot become a second unbounded worker.
6. **The browser is static, public code** with no engine, WASM binary or
   provider key; every classification and engine call stays on the server.

This is a scope decision, not an oversight. It is acceptable only while the
service analyses public games and has no private user data. The "when to add
auth" section below marks the point where it stops being acceptable.

## Identifiers: what each one authorizes

| Value | Secret? | What it grants |
|---|---|---|
| `job_id` | **Yes, effectively** - it is a bearer capability | `GET /api/jobs/{id}` and `POST /api/jobs/{id}/cancel` |
| `game_key` | No | Identifies a public Chess.com game; with `user_color`, reads the report and positions, and grades legal try-again candidates |
| `user_color` | No | Selects which side's report to return and whose flagged plies can be retried |
| `X-Request-ID` | No | Correlates log lines only; it authorizes nothing |
| `LLM_API_KEY` and other environment values | **Yes** | Must never appear in an ID, URL, response, log or browser bundle |

### `job_id`: anyone holding it can poll or cancel

`POST /api/analyze` creates the job with `uuid.uuid4()` (`server/api.py`). The
ID is 122 random bits, so guessing one is infeasible. That is the *only* thing
protecting job control: there is no session, no owner column, no ownership
check, and no way to revoke a leaked ID.

Concretely, anyone who has a `job_id` can:

- read its status, progress and persisted moves with `GET /api/jobs/{id}`;
- cancel it with `POST /api/jobs/{id}/cancel`, even if they did not create it.

The ID is therefore a **bearer capability**. Treat it like a password in a URL:
do not publish it, paste it into public issue trackers, or include it in
screenshots. The mitigating fact is that it carries no secret payload - leaking
it exposes one job, not an account or an API key.

### `game_key` and `user_color`: public report access

`GET /api/games/{game_key}/report?user_color=...` has no auth either. A
`game_key` is derived from public Chess.com data (the game UUID, or a URL/link
fallback), and `user_color` is public. **Anyone who knows the pair can read the
report.** That is expected: the report describes a public game, and C1 already
lists public games by username. The same pair also derives per-ply FENs from
A1's positions bridge and selects the report used by D1's try-again endpoint.

Do not treat C5 as a privacy boundary. If the project ever stores private games
or user-specific annotations, C5 needs an ownership check before that data is
served. The same applies to A1 and D1.

### Try-again reaches an engine, but not arbitrary input

`POST /api/games/{game_key}/try` lets anyone with the pair ask the API to
evaluate one legal candidate in a stored report. This is deliberate: the
candidate cannot choose a FEN, depth, MultiPV, engine path or threshold, and the
server re-validates the SAN against the reconstructed position before spending
engine time. The abuse controls are D1's separate try bucket, a process-wide
one-slot non-blocking engine guard, and a hard `TRY_TIMEOUT_SECONDS` timeout.
A busy slot is a truthful `503`, not an unbounded wait.

## Threat model

| Threat | Control | Residual risk |
|---|---|---|
| Guess a `job_id` to read or cancel someone else's job | UUID4 (122 random bits); server generates it; clients cannot choose it | A leaked ID is usable by anyone until the row is deleted; there is no revocation |
| Read someone else's game report | None, by design; `game_key` + `user_color` are public | No privacy promise; do not put private data in reports |
| Queue the same analysis repeatedly | Migration 3's partial unique index on `(game_key, user_color)` for active jobs; C2 reuses the winner | A finished/failed/cancelled job can be retried, which is intended |
| Burn CPU with new analyses or try-again requests | C7 limits `POST /api/analyze` to 5 requests/IP/hour and `POST .../try` to its own 60/IP/hour bucket by default; depth and MultiPV come from server/report config, not the request | IP-based limits are imperfect (NAT, IPv6 rotation, proxies), and both buckets are per API process |
| Occupy the API's try engine indefinitely | D1 holds one process-wide slot non-blockingly, bounds a search with `TRY_TIMEOUT_SECONDS` (default 20), and returns `503` when busy or timed out | A burst can make legitimate try-again requests see `503`; the API slot also competes with worker processes for CPU |
| Run an engine or call Chess.com from the browser | The browser bundle contains no engine/WASM and only calls `/api` and `/health`; `chess.js` is used locally for the legal-move picker, and the server revalidates every candidate | Browser dependencies and a reverse proxy's egress policy remain supply-chain/deployment concerns |
| Spoof `X-Forwarded-For` to reset the rate-limit bucket | The header is trusted only when the immediate peer is in `TRUSTED_PROXY_IPS`; only the rightmost entry is used; trust-all CIDRs are rejected | A misconfigured trust range that contains clients lets them spoof their address |
| Keep a job running forever | Per-move engine timeout (B5), cooperative cancellation (C4), and the heartbeat-based reaper (B4) | A hard process kill is covered by the reaper, not by the timeout alone |
| Exhaust memory with many rate-limit keys | The limiter remembers at most 10,000 clients and evicts the oldest | Eviction can reset a legitimate client's count |
| Leak secrets through IDs, responses or logs | IDs are random and carry no payload; `/health` exposes no paths or config; `.env` is gitignored; CORS does not allow credentials | Access logs contain request paths, including job IDs and game keys; protect log access and retention |
| SQL injection or command injection | SQL uses parameters; the engine receives only constructed FENs and UCI options. Reviewed and signed off in [`docs/safety-review.md`](safety-review.md) (D5) | Any future path that interpolates user input into SQL or a shell would break this; `tests/test_safety.py` trips on a shell |
| CSRF against job control | No cookies or ambient credentials are used; `allow_credentials=False`; job IDs are explicit capabilities | If cookie-based auth is added, CSRF protection becomes mandatory |
| Abuse of future LLM explanations | C6 is a fixed stub today and makes no LLM call | When Phase 5 wires a real model, `POST /api/explain` will need its own quota and cost control; C7 does not cover it |

## Abuse controls in detail

### Rate limiting (C7 + D1)

There are two separate sliding windows:

- `POST /api/analyze`: 5 requests per client IP per hour
  (`RATE_LIMIT_ANALYSES_PER_HOUR`; `0` disables it). The check runs before the
  Chess.com lookup, so a denied request spends no upstream or engine work.
- `POST /api/games/{game_key}/try`: 60 requests per client IP per hour
  (`RATE_LIMIT_TRY_PER_HOUR`; `0` disables it), with its own counters. The check
  runs after cheap report/ply/SAN validation but before the engine.

Allowed responses and both `429`s carry `X-RateLimit-*` headers; a `429` also
carries `Retry-After`. The endpoints are documented in
[`docs/api.md`](api.md).

Every analyze request with a valid body counts, including one that reuses a
stored report, returns `404`, or fails upstream. A Pydantic `422` is rejected
before the limiter and is not counted. A try request counts only after the
report exists, the ply belongs to the requested colour, and the SAN is legal;
an engine-busy or timed-out request still consumed its bucket because it is a
genuine engine-bound request.

The counters are **in memory and per API process**. A restart resets them, and
a multi-worker deployment would multiply the effective budget. Moving the
counters to shared storage is a prerequisite for horizontal scaling. The
limiter's 10,000-key LRU bound stops unbounded growth, at the cost of sometimes
forgetting an old client.

### Fixed engine cost

An analyze request cannot choose depth or MultiPV; both come from server config.
A try request cannot choose depth or MultiPV either: it uses the values frozen
into the stored report. That keeps one request's cost bounded and prevents a
caller from asking for an arbitrarily deep search. The worker processes one job
at a time by default, and the timeout/reaper pair bounds a stuck job. The API's
try path has a separate one-slot guard and timeout, so it cannot become a second
unbounded worker.

### Request validation

Usernames are restricted to `[A-Za-z0-9_-]{1,50}` before any upstream request.
Job IDs are capped at 64 characters and report keys at 2048. The analyze, try
and explain bodies use `extra="forbid"`; try accepts only `user_color`, `ply` and
`move_san`, then parses the SAN against the reconstructed position. Explain's
`pv`/`tags` lists are bounded. These are field-level guards, not a substitute
for a reverse-proxy body-size limit.

### CORS

`CORS_ORIGINS` is an allowlist, credentials are disabled, and only `GET`/`POST`
are allowed. The rate-limit headers are explicitly exposed so the browser can
read the retry delay. In production, use the exact frontend origin for a split
deployment, or no CORS origins when the built frontend and API share an origin.
CORS is a browser policy, **not** access control: a non-browser client can call
the API directly. It also does not prevent a leaked capability URL from being
used.

## Rules for IDs

1. **Generate IDs with UUID4.** Do not switch to sequential IDs, timestamps, or
   hashes of user input.
2. **Never accept a client-supplied job ID.** The server creates it in C2.
3. **Never put secrets or personal data in an ID.** No email, username, IP,
   API key, or signed payload. A UUID4 has no structure to leak.
4. **Do not use a signed token as an ID.** If authentication arrives, keep the
   public ID opaque and put ownership in a server-side row; do not make the ID
   itself the proof of ownership.
5. **`game_key` is public, not secret.** It may contain a Chess.com URL. Do not
   add secret material to it, and do not treat it as an authorization token.
6. **Do not log secrets.** The request logger records method, path, status and
   duration; paths can contain capability IDs, so logs need the same care as
   the IDs themselves. Request bodies and headers are not logged.

## Deployment requirements

These are operational controls the code does not enforce. They are required
for the no-auth model to be acceptable:

- Terminate TLS at Caddy (or another reverse proxy); do not expose uvicorn
  directly to the internet.
- Set `TRUSTED_PROXY_IPS` to the proxy's address or network, never to a range
  that also contains clients. The default loopback value covers Caddy on the
  same host; Docker needs the proxy network (for example `172.16.0.0/12`).
- Keep `CORS_ORIGINS` to the exact frontend origin for a split deployment, or
  leave it empty when the built frontend and API share an origin. Do not set
  `allow_credentials`.
- Serve the built `web/dist/` and reverse-proxy `/api` and `/health` to uvicorn;
  use an SPA fallback for application routes, not for API paths.
- Apply a coarse global request limit and body-size limit at the proxy. The
  per-IP limits in C7/D1 are scoped to analyze and try, not every endpoint.
- Budget CPU for the try engine in the API process (`TRY_TIMEOUT_SECONDS`,
  default 20, plus one slot) alongside the worker processes. A busy slot returns
  `503`; that is deliberate backpressure, not a failure to retry at any cost.
- Serve app pages with `Referrer-Policy: no-referrer` (or an equally strict
  policy), so a capability URL in the address bar is not sent to third-party
  origins. Avoid third-party embeds on report pages.
- Protect `.env`, the SQLite file and its backups, and logs. The database holds
  public Chess.com data, but the `.env` holds the future LLM key. Remember that
  every `VITE_*` value is compiled into the public browser bundle.
- Keep `/api/docs` public only if exposing the schema is acceptable; it
  contains no secrets.

## When to add authentication

The no-auth model stops being acceptable when any of these becomes true:

- reports become private, or users can save annotations or drill sets;
- the service needs per-user history, deletion, or higher quotas;
- a real LLM is wired in and explanation cost needs a per-user budget;
- abuse cannot be handled by IP limits and a reverse proxy;
- the project becomes a product rather than a portfolio piece.

At that point, the minimum design is:

- accounts and sessions (or OAuth), with CSRF protection if cookies are used;
- an `owner_id` on jobs and reports, and an authorization check on poll,
  cancel and report reads;
- per-user quotas in shared storage, not per-process counters;
- revocable credentials and an audit trail for job control;
- a deliberate sharing model (for example, explicit share links) rather than
  "whoever has the UUID".

Do not bolt ownership onto the existing UUID by making it a signed token. Keep
the public ID opaque; add authorization beside it.

## C8 acceptance and deployment checklist

- [x] `job_id` is generated with `uuid.uuid4()` in `server/api.py`.
- [x] IDs carry no secrets; the server does not accept client-supplied job IDs.
- [x] This document states that anyone with a `job_id` can poll or cancel it.
- [x] This document states that anyone with a `game_key` and `user_color` can
      fetch the report and positions, and grade try-again candidates, and that
      neither is a secret.
- [x] Both the analysis and try rate limits, their separate buckets, and the
      per-process limitation are documented.
- [x] The try endpoint's one-slot guard, hard timeout and `503` backpressure are
      documented.
- [x] The `X-Forwarded-For` trust rule and its deployment requirement are
      documented.
- [x] The CORS boundary is an allowlist with credentials disabled, and the
      frontend/same-origin deployment cases are documented.
- [x] The browser bundle contains no engine, WASM binary or provider key.
- [ ] Deployment controls above (TLS, proxy limits, referrer policy, log
      protection) are applied in the deployed environment. This is an
      operational follow-up, not part of the C8 write-up.
