# API contract — Phase 2

The API is versioned by its URL prefix (`/api`) and documented live at
`/api/docs` (OpenAPI at `/api/openapi.json`). This file records the decisions
behind each endpoint; the OpenAPI schema is the field-by-field source of truth.

> **Status:** C1 (game list) is implemented. The queue-facing endpoints (C2–C5)
> and the explain stub (C6) are not yet mounted.

---

## C1 — `GET /api/player/{username}/games`

Lists a player's recent Chess.com games, newest first, normalised for the
browser. Served through the Chess.com client's on-disk cache, so a repeat call
is a disk read rather than a second trip upstream.

### Path parameter

| Name | Rules |
|---|---|
| `username` | `[A-Za-z0-9_-]{1,50}`, case-insensitive |

The pattern is enforced *before* any upstream request. Chess.com usernames
cannot contain `/`, so constraining the parameter keeps a crafted name from
being interpolated into a different upstream URL.

### Query parameters

| Name | Default | Rules | Why |
|---|---|---|---|
| `months` | `3` | 1–120 | Bounds how many monthly archives a first request walks. |
| `max_games` | `50` | 1–500 | Bounds the response; the client stops as soon as it has this many. |
| `time_class` | all | `bullet` / `blitz` / `rapid` / `daily` | The UI's filter. |
| `include_bots` | `true` | bool | A bot game is still a game you played, but its rating is meaningless. |
| `include_from_position` | `false` | bool | Such games have no ply 1, which corrupts the ply-based phase labels. |

The defaults match the client's (`server/chesscom.py`), so the endpoint cannot
silently disagree with the library underneath it.

### Response — `200`

```json
{
  "username": "beast-ash",
  "count": 2,
  "games": [
    {
      "game_key": "chesscom:7515eb15-befd-11f1-8dbb-feb0c301000f",
      "url": "https://www.chess.com/game/live/184741950578",
      "white": {"username": "BEAST-ASH", "rating": 1171, "result": "win", "title": null},
      "black": {"username": "Gopinath_27", "rating": 1146, "result": "checkmated", "title": null},
      "result": "1-0",
      "time_class": "rapid",
      "time_control": "600",
      "rated": true,
      "rules": "chess",
      "end_time": 1791013370,
      "is_bot": false,
      "from_position": false,
      "user_color": "white"
    }
  ]
}
```

Field notes:

- **`game_key` is opaque.** It is the A3 fallback chain (`uuid` → `url` →
  `link` → digest), prefixed with its provenance. Older games have no `uuid`,
  so this is the only identifier that is always present. Callers must not parse
  it.
- **`user_color`** is which side the requested username played, matched
  case-insensitively. It is `null` only if an upstream game omits that player —
  a defensive value, not a normal case.
- **`result`** is derived from the two per-player results. Chess.com does not
  put `1-0`/`0-1` on the game object; an unfinished game is `null` rather than
  a guessed draw.
- **`is_bot` / `from_position`** are included even though the default policy
  filters from-position games out, so a caller that opts in can label them.
- **No PGN.** The list is fetched on every page load; the PGN is needed only
  when analysis is requested (C2), which refetches it through the same cache.
  Keeping it out keeps the payload proportional to the number of games, not
  the number of moves.

### Errors

| Status | When |
|---|---|
| `404` | Chess.com has never heard of the player (`PlayerNotFound`). |
| `422` | The username or a query parameter fails validation. |
| `502` | Chess.com failed or rate-limited us (`ChessComError`). A bad gateway, not a bug in this service. |

A player who exists but has no games is **not** an error: the response is
`200` with `count: 0`.

### Caching

The endpoint inherits A3's response cache:

- archives list — 10-minute TTL (it grows);
- a month — 1-hour TTL (past months are immutable, the current one is not);
- 404s are never cached (a transient gap must not become permanent).

`CHESSCOM_CACHE_DIR` enables it; unset means every call goes upstream. The
tests assert the cache path with a fixed clock and a mock transport, so they
never touch the network or wait.

---

## Not here yet

| ID | Endpoint | Milestone |
|---|---|---|
| C2 | `POST /api/analyze` | next |
| C3 | `GET /api/jobs/{id}` | next |
| C4 | `POST /api/jobs/{id}/cancel` | C |
| C5 | `GET /api/games/{game_key}/report` | C |
| C6 | `POST /api/explain` (stub) | C |
| C7 | per-IP rate limiting | C |

The breakdown writes `game_uuid` for C2/C5; the identifier is **`game_key`**
for the reason above — not every game has a `uuid`, and the database is keyed on
the fallback chain.
