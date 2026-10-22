# The job queue (Phase 2 B2)

`server/queue.py` owns the **atomic claim** and the status transitions around it.
This is the concurrency core of Phase 2, and the reasoning matters far more than
the SQL: the failure it prevents is **silent**.

- **Implementation:** `server/queue.py`
- **Tests:** `tests/test_queue.py`
- **Schema:** `jobs` in `server/db.py` (heartbeat column added by migration 2)

## The failure being prevented

Two workers holding the same job raises nothing. Both analyse the game, both
insert the same `moves` rows, and the second writer overwrites the first. The
symptom is duplicated engine work and a report that is subtly whichever worker
finished last — with no error anywhere to point at.

That is why the claim is the one thing worth being obsessive about.

## Where the race actually is

The obvious implementation has a gap:

```
SELECT id FROM jobs WHERE status='queued' LIMIT 1   -- both workers see job-7
UPDATE jobs SET status='running' WHERE id = 7       -- both "claim" job-7
```

Between the `SELECT` and the `UPDATE`, a second worker can select the same row.
The window is microseconds wide, which makes it *more* dangerous, not less: it
will not show up in a quick manual test and will show up under load.

`tests/test_queue.py::test_two_workers_never_claim_the_same_job` demonstrates it
directly — a naive read-then-write claim, run against the same harness, produces
duplicate claims; `claim_job` produces none.

## The fix: one statement, under a write lock

`_CLAIM_SQL` selects the job and takes it in a **single** `UPDATE … RETURNING`:

```sql
UPDATE jobs
   SET status='running', worker_id=:worker_id, started_at=:now,
       heartbeat_at=:now, attempts=attempts+1, updated_at=:now, error=NULL
 WHERE id = (SELECT id FROM jobs WHERE status='queued'
              ORDER BY created_at, id LIMIT 1)
RETURNING *
```

There is no window because there is no second statement. Two supporting settings
make it genuinely atomic:

| Mechanism | Where | What it does |
|---|---|---|
| `BEGIN IMMEDIATE` | `claim_job` | takes the **write** lock before the statement runs. sqlite3's default is a *deferred* transaction, which upgrades to a write lock only on first write — reintroducing exactly the gap above. |
| `PRAGMA busy_timeout` | `server.db.connect` | makes the second worker **wait** for the lock instead of raising `database is locked`. |
| `isolation_level=None` | `server.db.connect` | autocommit, so `BEGIN IMMEDIATE` is ours to issue explicitly rather than sqlite3's implicit deferred begin. |

The lock is held for one statement, so contenders block for microseconds, not for
the duration of an analysis.

### Why `ORDER BY created_at, id`

The `id` tiebreak is not cosmetic. `created_at` is second-resolution, so two jobs
enqueued in the same second tie — and without a tiebreak SQLite is free to return
either row, so two workers can select the same one and the loser then finds
nothing to update.

## `Claim`: two failures, told apart

`claim_job` returns a `Claim` rather than `Optional[Row]`, because "nothing to do"
and "someone beat me to it" demand opposite reactions:

| `reason` | Meaning | Worker should |
|---|---|---|
| `claimed` | you have a job (`.job` is the full row, via `RETURNING *`) | run it |
| `empty` | the queue is genuinely drained | sleep, then poll |
| `lost_race` | a job exists but was taken | retry **immediately** |

Collapsing these into `None` makes the worker either spin needlessly or sleep
when progress was being made.

## The heartbeat

`touch_job` / `record_progress` refresh `heartbeat_at`. **The reaper (B4) keys
off this, not `started_at`.** The distinction:

- a large analysis may legitimately exceed the timeout and is doing so *healthily*;
- a dead worker's job stops recording progress.

`started_at` cannot tell those apart; `heartbeat_at` can. `record_progress`
advances the ply counter and the heartbeat in **one statement**, because
"progress advanced" and "the job is alive" are the same fact and should never be
observable apart.

A freshly claimed job has `heartbeat_at == started_at`, so it is never stale on
arrival.

## Transitions

| Function | Effect |
|---|---|
| `claim_job` | `queued → running`; bumps `attempts`, clears `error` |
| `record_progress` / `touch_job` | liveness only; no status change |
| `complete_job` | `→ done`; stamps `finished_at` |
| `fail_job` | `→ failed`; stores a **truncated** reason (it may embed a traceback) |
| `requeue_job` | `→ queued`; clears `worker_id`/`started_at`/`heartbeat_at` |

`requeue_job` clears the worker fields rather than keeping them: a stale
`worker_id` on a queued job makes it look like someone still owns it.

## Not here

The **reaper** and the **hard timeout** are **B4 / B5**. `MAX_ATTEMPTS` is stated
here so the retry policy has a single home, but it is the reaper that enforces it.
