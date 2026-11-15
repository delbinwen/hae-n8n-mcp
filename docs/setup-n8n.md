# Set up n8n

Do this on n8n 2.43.0 or newer. The reason for that version is in the README. Logic from the previous revision was exercised on n8n 2.46.4 with manual runs. This revision, and any MCP client call, still needs a pass on your instance. After you import, open each workflow and confirm the nodes are recognized before you publish.

## 1. Tables and credentials

Create the tables in [data-tables.md](data-tables.md) in the project where the workflows will live.

Create two credentials in that project. Generate each secret locally, for example `openssl rand -hex 32`. Store it in a password manager. Do not put it in a workflow, a screenshot, or git.

| Credential name in the JSON | Credential type | Used by |
| --- | --- | --- |
| `HAE Webhook Header Auth` | Header Auth | 01, node `HAE Webhook` |
| `HAE MCP Bearer` | Bearer Auth | 03, node `HAE Health MCP Server` |

For the header credential, pick a header name you control, such as `X-HAE-Token`, and a long random value. Health Auto Export must send that same header. For the bearer credential, the value is the token only. Clients send `Authorization: Bearer <token>`.

The workflow JSON names those credentials and does not contain credential ids. On the 2.46.4 import, n8n attached an existing credential of the same type (any Header Auth on the webhook, any Bearer Auth on the MCP trigger) even when the name did not match. After import, open both triggers and set the credential to the name in the table. A uniquely named credential makes the wrong selection obvious.

## 2. Import

Import in this order:

1. [workflows/01-receiver.json](../workflows/01-receiver.json) — `HAE 01 - Receiver (Health Auto Export webhook)`
2. [workflows/02-dedup.json](../workflows/02-dedup.json) — `HAE 02 - Dedup (health_raw -> health_points / health_workouts)`
3. [workflows/03-query-mcp.json](../workflows/03-query-mcp.json) — `HAE 03 - Query MCP server`

On **each** workflow, check these. The 2.46.4 import kept them on one path and dropped timezone and execution order on another, so do not trust the canvas until you have looked:

- **Settings → Timezone.** The file says `Asia/Taipei`. Set the zone you actually want, and use the same value on all three. This zone drives the 03:30 cleanup clock, the local calendar dates (`date_local`), and `updated_at`. It does not change the UTC `received_at` stamp. If the setting is missing, n8n uses the instance default. These templates add `timezone_warning` when that zone is `UTC`. Changing the zone later does not rewrite `date_local` values already stored.
- **Settings → Execution order** must stay **v1**. Do not switch the workflow to v0. Workflow 02 no longer depends on canvas position for the watermark: a one-item loop saves it after the upserts return. v1 is still what the file and the dedup comments assume.
- **Settings → executions.** The file does not save successful production executions, manual executions, or error executions. Confirm that survived import. A failed run is not kept. To debug one, temporarily enable saving of failed executions, reproduce it, then turn saving off and delete that execution. It holds the health payload. See the README security notes.
- **Data tables.** Every Data Table node is **By ID** with a placeholder such as `__HEALTH_RAW_TABLE_ID__` and a cached name so you can see which table was intended. Open each one and pick the table. An import test showed **By Name** is a case-insensitive substring match (`LIKE '%name%'`, and `_` is a single-character wildcard) and the first hit wins: `health_raw` bound to `test_health_raw` with no error. That is why these templates do not use By Name. A missing id fails the node; it does not guess.

Workflow 01's cleanup reads `hae_dedup_state` as well as `health_raw`. Pick both. Workflow 02 has seven `hae_dedup_state` nodes: `Read lock`, `Take lock`, `Confirm lock`, `Read watermark`, `Re-read watermark`, `Save watermark if newer`, and `Release lock`. Pick the same table in all of them.

## 3. Paths

| Workflow | Node | Replace |
| --- | --- | --- |
| 01 | `HAE Webhook` | `hae-ingest-CHANGE-ME` |
| 03 | `HAE Health MCP Server` | `hae-mcp-CHANGE-ME` |

Use a long random string for each path, different from the tokens. The production URLs are:

- `https://<your-n8n-host>/webhook/<ingest-path>`
- `https://<your-n8n-host>/mcp/<mcp-path>`

Use the production URLs, not the test URLs (`/webhook-test/...`). Health Auto Export should call the webhook only after workflow 01 is published.

Workflow 03's four tool nodes already point at this workflow with `={{ $workflow.id }}`. n8n evaluates a parameter expression only when the stored string starts with `=`, which is why the value is written that way. The Call n8n Workflow Tool reads `workflowId.value` through `getNodeParameter`, which resolves that expression when the tool runs.

A call from the tool runs the **published** copy of workflow 03, not an unpublished draft. Publish 03 before you test a tool. If opening a tool node clears its input mapping, set the workflow picker to this same workflow again. The inputs are the fields on the `Tool Input` node: `tool`, `metric`, `start_date`, `end_date`, `granularity`, `workout_id`, `include_series`.

If a tool call is rejected because this workflow does not allow callers, open **Settings** and allow it to be called by workflows in the same project. The export does not set that policy. The default on your instance may already allow it.

## 4. Publish order

1. Publish workflow 01. Send one Health Auto Export post, or a synthetic POST, and confirm a `health_raw` row.
2. Publish workflow 02. Wait for the 30-minute tick, or run it manually once the newest raw row is at least 2 minutes old. Confirm `health_points` and, if you sent workouts, `health_workouts`, and a `health_raw_watermark` row whose `value` ends in `Z`. Also confirm a `health_raw_dedup_lock` row with `lock_owner` empty and `lock_until` `1970-01-01T00:00:00.000Z`. That means the run released its lock. A run with no new raw rows should leave the lock row released too.
3. Publish workflow 03. Connect a client as in [mcp-client.md](mcp-client.md).

A synthetic POST body, with no real measurements:

```json
{
  "data": {
    "metrics": [
      {
        "name": "example_metric",
        "units": "count",
        "data": [
          {
            "qty": 1,
            "date": "2000-01-01 00:00:00 +0000",
            "source": "Example Source"
          }
        ]
      }
    ],
    "workouts": [
      {
        "id": "example-workout-0001",
        "name": "Example Walk",
        "start": "2000-01-01 00:00:00 +0000",
        "end": "2000-01-01 00:10:00 +0000",
        "duration": 600
      }
    ]
  }
}
```

Send it with the header you configured. Expect `{"ok":true}` only after the row is stored. A storage failure is not a 200. After dedup, the point key is `example_metric|2000-01-01T00:00:00.000Z` (`toISO()` keeps the milliseconds) and `example-workout-0001` is one workout with `duration_min` 10. The reply body is that static JSON. It does not include the stored row.

## What the receiver stores

The webhook node (typeVersion 2.2) puts the POST JSON on `$json.body`. Workflow 01 stringifies that object into `health_raw.body`. Workflow 02 reads `data.metrics[]` and `data.workouts[]` only.

`data.metrics[].data[]` dates must be `yyyy-MM-dd HH:mm:ss ZZZ` (Health Auto Export's usual form) or ISO. Samples with no parseable date are skipped and counted in the watermark `note`.

## Dedup lock

Workflow 02 runs every 30 minutes. A large backfill can take longer than that. Without a lock, two runs could read different raw snapshots, an older run could overwrite a sample that a newer run had just written, and the watermark could move backwards. The lock and a forward-only watermark prevent that. Both use only the `hae_dedup_state` data table, with no extra credential or service.

What a run does:

1. `Read lock` reads the `health_raw_dedup_lock` row. `Check lock is free` ends the run before it reads `health_raw` or writes anything when `lock_owner` is another execution's id and `lock_until` is a UTC stamp still in the future.
2. Otherwise `Take lock` writes this execution's id and `lock_until` = now + 120 minutes (`LOCK_MINUTES`). `Settle 3 seconds` waits, `Confirm lock` reads the row again, and `We hold the lock` continues only if the id read back is this execution's. If two runs start together, both write and only the last writer reads its own id back.
3. Points and workouts are upserted one at a time, as before.
4. `Re-read watermark` reads the stored watermark after those upserts. `Save watermark if newer` runs only when this run's watermark is later than the stored one, so it never moves backwards.
5. The last item of the loop, `Release lock`, sets `lock_owner` to `""` and `lock_until` to `1970-01-01T00:00:00.000Z`, but only on the row whose `lock_owner` is still this execution. A run that lost its lock does not clear a newer run's lock.

When things go wrong:

- **A run fails** (an upsert error, a restart, a crash). The lock stays until `lock_until`. Ticks in that window stop at `Check lock is free`, so dedup pauses for at most 120 minutes and then resumes from the watermark. Raw rows are not lost: cleanup only deletes rows the watermark has passed.
- **A run lasts longer than 120 minutes.** The next tick after expiry takes the lock and the two runs overlap. The forward-only watermark still holds, but the older run can overwrite points with stale values while it finishes. If your backfills take that long, raise `LOCK_MINUTES` in `Check lock is free` above your longest run. A larger value only means a longer pause after a crash.
- **A stuck lock you want cleared now.** First make sure no workflow 02 execution is running (**Executions**, or wait past `lock_until`). Then set `lock_until` to `1970-01-01T00:00:00.000Z` on the `health_raw_dedup_lock` row, or delete that row. The next run recreates it.
- **A manual run while a scheduled run is working** stops at `Check lock is free`. That is expected.

Limits: Data Tables have no compare-and-swap, so the lock is read, write, pause, read back. Two runs whose read and write straddle each other by more than the 3-second pause could both proceed. The schedule fires 30 minutes apart, so this needs a manual run started at almost the same moment as a tick. The first run against an empty table could also create two lock rows if two runs start together. `Read lock` and `Confirm lock` read only the first match. If you see two `health_raw_dedup_lock` rows, delete both while no run is working.

## Upgrade: add the dedup lock columns

For a `hae_dedup_state` table created from an earlier revision:

1. Unpublish workflow 02 and let any running execution finish.
2. Add two columns to `hae_dedup_state`, both type `string`: `lock_owner` and `lock_until`. Leave the existing `health_raw_watermark` row as it is.
3. Import the new workflow 02, or replace the old one. Pick the `hae_dedup_state` table in all seven state nodes listed under Import, and the other tables as before.
4. Check **Settings → Timezone** and **Execution order v1** again.
5. Publish it. After the first run, confirm the `health_raw_dedup_lock` row exists and is released, as in step 2 of the publish order.

Workflow 01 does not change for this upgrade. Its cleanup reads only the watermark row.

## Upgrade from offset-form received_at

Older copies of workflow 01 stored `received_at` as local time with a numeric offset, for example `2000-01-01T08:00:00.000+08:00`, and workflow 02 compared that text to the watermark. This template does not do that.

Text order of the new `Z` form does not match text order of the old offset form. Two stalls show up if you mix them:

- A watermark like `...+08:00` can sort **after** a newer `Z` row (`...T15:00:00.000+08:00` is 07:00Z, but it sorts after `...T07:08:00.000Z`). Dedup's lower bound and its 2-minute upper bound are both text comparisons, so those rows are skipped until the strings happen to line up. For a UTC+8 offset that can be hours.
- Cleanup used to compare `received_at` with a UTC cutoff the same way. A positive offset can look newer and survive past 3 days. A negative offset can look older and be deleted early (`...T20:00-05:00` sorts before a next-day `Z` cutoff).

The cleanup in this revision refuses a watermark that is not the fixed UTC stamp and then deletes nothing. That avoids the early-delete case. It does not fix the dedup stall. Do not leave a mix.

Dedup upserts are idempotent, so reprocessing is safe. Do not create a second table whose name contains `health_raw` or the other three names if you still have an old workflow that uses By Name. These templates use ids, but a backup table is easy to pick by mistake.

1. With the **old** workflow 02 still published, let it finish any offset-form rows you still care about, or accept that you will reprocess them after the reset.
2. Unpublish workflow 01 and workflow 02. Leave them unpublished while any offset-form `received_at` remains. Publishing the new 01 first would let it keep accepting posts, and a cleanup run against an offset watermark deletes nothing, so raw rows would pile up, but new posts would be UTC beside old offset rows.
3. Delete the `health_raw_watermark` row, or set its `value` to `1970-01-01T00:00:00.000Z`.
4. Delete remaining `health_raw` rows, or rewrite every `received_at` to `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'` in true UTC.
5. Publish workflow 01, then workflow 02. The next dedup run rewrites points and workouts for the raw rows it reads. `received_at` copied onto those flattened rows becomes the raw row's stamp. Old offset text already stored on `health_points.received_at` or `health_workouts.received_at` is display-only and does not move the watermark.

`updated_at` is still local, on purpose. It is not read back for ordering.

## What was and was not run

A re-test of commit `82c144d` on n8n 2.46.4 passed the reply-after-insert path, the one-record loop with the watermark last, an idempotent second dedup run, cleanup at the earlier of now−3 days and the watermark (no delete when the watermark is missing or not a UTC stamp), all four tools including recovery null at 120 seconds, empty source not counted, a Monday week label, and no `timezone_warning` with timezone `Asia/Taipei`. `$workflow.id` was not called, because that needs workflow 03 published and an MCP client.

That same run stored a POST with no body (`kind` `unknown`, `body` null) and returned 200. The current workflow 01 rejects that with 400 and does not insert. Workflow 02 now moves the watermark past rows it skips for a missing `payload_id` or `body`. Those two edits have not been imported again. Neither has the dedup lock or the forward-only watermark. Their Code nodes were run outside n8n against mocked inputs. The node wiring and Data Table behaviour were not. On your instance, still confirm:

- Each Data Table node shows the table you picked, not a placeholder and not a similarly named table.
- A failed insert does not return `{"ok":true}`.
- `list_metrics` returns the Code node's JSON when called through MCP (Execute Workflow Trigger 1.3 collects sub-workflow outputs differently from 1.2).
- A number column accepts an empty `qty` when a sample has no quantity.
- The workflow timezone you chose is the zone used in `date_local`.
- A workflow 02 run with no new raw rows still reaches `Release lock` (`Read new health_raw rows` has **Always Output Data** on).
- `Release lock` (a Data Table **Update**) leaves the row alone when `lock_owner` is another id, and does not fail when it matches nothing.
- A second manual run of workflow 02 started while one is working stops at `Check lock is free`.
- After a failed run, the lock row keeps `lock_owner` until `lock_until`, then the next tick takes it.
