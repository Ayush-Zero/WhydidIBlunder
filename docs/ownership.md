# Ownership and abuse model (Phase 2 C8)

**Status:** accepted for v1 on 2026-10-29.
**Scope:** the Phase 2 API and worker in `server/`; there is no frontend yet.
**Related:** [`docs/api.md`](api.md), [`docs/storage.md`](storage.md),
[`docs/safety-review.md`](safety-review.md), and `server/ratelimit.py`.

## The decision

WhydidIBlunder v1 has **no accounts and no authorization layer**. It is a
portfolio-scoped, public analysis tool: enter a Chess.com username, analyse a
public game, read the report. The ownership model is deliberately small:

1. **Unguessable job IDs** protect job control (poll and cancel).
2. **A per-IP rate limit** protects the expensive analysis endpoint.
3. **Public Chess.com data** is the only game data the API exposes.
4. **No secrets go into IDs.** An ID is an identifier, never a credential.

This is a scope decision, not an oversight. It is acceptable only while the
service analyses public games and has no private user data. The "when to add
auth" section below marks the point where it stops being acceptable.

## Identifiers: what each one authorizes

| Value | Secret? | What it grants |
|---|---|---|
| `job_id` | **Yes, effectively** - it is a bearer capability | `GET /api/jobs/{id}` and `POST /api/jobs/{id}/cancel` |
| `game_key` | No | Identifies a public Chess.com game; with `user_color`, reads the stored report |
| `user_color` | No | Selects which side's report to return |
| `X-Request-ID` | No | Correlates log lines only; it authorizes nothing |
| `LLM_API_KEY` and other environment values | **Yes** | Must never appear in an ID, URL, response, or log |

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
lists public games by username.

Do not treat C5 as a privacy boundary. If the project ever stores private games
or user-specific annotations, C5 needs an ownership check before that data is
served.

## Threat model

| Threat | Control | Residual risk |
|---|---|---|
| Guess a `job_id` to read or cancel someone else's job | UUID4 (122 random bits); server generates it; clients cannot choose it | A leaked ID is usable by anyone until the row is deleted; there is no revocation |
| Read someone else's game report | None, by design; `game_key` + `user_color` are public | No privacy promise; do not put private data in reports |
| Queue the same analysis repeatedly | Migration 3's partial unique index on `(game_key, user_color)` for active jobs; C2 reuses the winner | A finished/failed/cancelled job can be retried, which is intended |
| Burn CPU with new analyses | C7 limits `POST /api/analyze` to 5 requests/IP/hour by default; depth and MultiPV come from server config, not the request | Only analyze is limited; list/report/explain and `/health` are not. IP-based limits are imperfect (NAT, IPv6 rotation, proxies) |
| Spoof `X-Forwarded-For` to reset the rate-limit bucket | The header is trusted only when the immediate peer is in `TRUSTED_PROXY_IPS`; only the rightmost entry is used; trust-all CIDRs are rejected | A misconfigured trust range that contains clients lets them spoof their address |
| Keep a job running forever | Per-move engine timeout (B5), cooperative cancellation (C4), and the heartbeat-based reaper (B4) | A hard process kill is covered by the reaper, not by the timeout alone |
| Exhaust memory with many rate-limit keys | The limiter remembers at most 10,000 clients and evicts the oldest | Eviction can reset a legitimate client's count |
| Leak secrets through IDs, responses or logs | IDs are random and carry no payload; `/health` exposes no paths or config; `.env` is gitignored; CORS does not allow credentials | Access logs contain request paths, including job IDs and game keys; protect log access and retention |
| SQL injection or command injection | SQL uses parameters; the engine receives only constructed FENs and UCI options. Reviewed and signed off in [`docs/safety-review.md`](safety-review.md) (D5) | Any future path that interpolates user input into SQL or a shell would break this; `tests/test_safety.py` trips on a shell |
| CSRF against job control | No cookies or ambient credentials are used; `allow_credentials=False`; job IDs are explicit capabilities | If cookie-based auth is added, CSRF protection becomes mandatory |
| Abuse of future LLM explanations | C6 is a fixed stub today and makes no LLM call | When Phase 5 wires a real model, `POST /api/explain` will need its own quota and cost control; C7 does not cover it |

## Abuse controls in detail

### Rate limiting (C7)

`POST /api/analyze` uses a sliding window of 5 requests per client IP per hour
(`RATE_LIMIT_ANALYSES_PER_HOUR`; `0` disables it). The check runs before the
Chess.com lookup, so a denied request spends no upstream or engine work.
Allowed responses and the `429` carry `X-RateLimit-*` headers; the `429` also
carries `Retry-After`.

Every analyze request with a valid body counts, including one that reuses a
stored report, returns `404`, or fails upstream. A Pydantic `422` is rejected
before the limiter and is not counted. This is documented in
[`docs/api.md`](api.md#c7---rate-limiting-on-post-apianalyze).

The counters are **in memory and per API process**. A restart resets them, and
a multi-worker deployment would multiply the effective budget. Moving the
counters to shared storage is a prerequisite for horizontal scaling. The
limiter's 10,000-key LRU bound stops unbounded growth, at the cost of sometimes
forgetting an old client.

### Fixed analysis cost

The request body cannot choose depth or MultiPV; both come from server config.
That keeps one request's engine cost bounded and prevents a caller from asking
for an arbitrarily deep search. The worker processes one job at a time by
default, and the timeout/reaper pair bounds a stuck job.

### Request validation

Usernames are restricted to `[A-Za-z0-9_-]{1,50}` before any upstream request.
Job IDs are capped at 64 characters and report keys at 2048. The analyze and
explain bodies use `extra="forbid"`, and explain's `pv`/`tags` lists are
bounded. These are field-level guards, not a substitute for a reverse-proxy
body-size limit.

### CORS

`CORS_ORIGINS` is an allowlist, credentials are disabled, and only `GET`/`POST`
are allowed. CORS is a browser policy, **not** access control: a non-browser
client can call the API directly. It also does not prevent a leaked capability
URL from being used.

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
- Keep `CORS_ORIGINS` to the exact frontend origin in production. Do not set
  `allow_credentials`.
- Apply a coarse global request limit and body-size limit at the proxy. C7 is
  scoped to new analyses, not to every endpoint.
- Serve app pages with `Referrer-Policy: no-referrer` (or an equally strict
  policy), so a capability URL in the address bar is not sent to third-party
  origins. Avoid third-party embeds on report pages.
- Protect `.env`, the SQLite file and its backups, and logs. The database holds
  public Chess.com data, but the `.env` holds the future LLM key.
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
      fetch the report, and that neither is a secret.
- [x] The rate limit and its per-process limitation are documented.
- [x] The `X-Forwarded-For` trust rule and its deployment requirement are
      documented.
- [ ] Deployment controls above (TLS, proxy limits, referrer policy, log
      protection) are applied in the deployed environment. This is an
      operational follow-up, not part of the C8 write-up.
