# Data tables

Create these four tables in the **same n8n project** as the workflows. n8n adds `id`, `createdAt`, and `updatedAt` itself. Do not add those columns.

Column types are the Data Table types `string` and `number`. Do not use the date type for timestamps. The workflows compare several of them as text, which is only safe for the formats below.

Names must be exactly:

- `health_raw`
- `health_points`
- `health_workouts`
- `hae_dedup_state`

The workflow nodes do **not** look tables up by name. Each one is **By ID** with a placeholder (`__HEALTH_RAW_TABLE_ID__` and the three siblings) and `cachedResultName` set to the table you should pick. After import, open every Data Table node and choose that table.

An import on n8n 2.46.4 showed why By Name is not used. The node's name mode is a case-insensitive substring match, `_` matches any single character, and the first row wins. A lookup for `health_raw` bound `test_health_raw` and did not fail. Do not assume an ambiguous name errors at runtime. Pick the id.

## health_raw

Written by workflow 01, read by workflow 02. Workflow 01 deletes rows whose `received_at` is more than 3 days old.

| Column | Type | Notes |
| --- | --- | --- |
| payload_id | string | `hae-` plus the n8n execution id |
| received_at | string | Fixed-width UTC `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`, for example `2000-01-01T00:00:00.000Z`. Compared as text |
| kind | string | `metrics`, `workouts`, and `ecg` joined by `+` when those arrays are non-empty, otherwise `unknown` |
| size_chars | number | Length of `body` |
| body | string | Full JSON body of the Health Auto Export POST |

`kind` including `ecg` only records that the array was present. Workflow 02 does not flatten ECG.

## health_points

Written by workflow 02, read by workflow 03. One row per metric and sample timestamp. A later payload with the same key overwrites the row.

| Column | Type | Notes |
| --- | --- | --- |
| key | string | Upsert key: `metric\|ts` |
| metric | string | Health Auto Export metric name, for example `step_count` |
| ts | string | Sample time, UTC, from Luxon `toUTC().toISO()` |
| ts_local | string | Same instant, ISO in the workflow timezone |
| date_local | string | `YYYY-MM-DD` in the workflow timezone |
| source | string | Source name, or `""` when missing. Stored, not part of the key |
| units | string | Sample units, otherwise the metric units |
| qty | number | `qty`, else `Avg`, else `totalSleep`, else `asleep`. Empty when none of those exist |
| extra | string | JSON of the other sample fields (`Min`, `Max`, sleep stages, and so on), or `""` |
| payload_id | string | Last `health_raw` payload that wrote this row |
| received_at | string | That payload's `received_at` (UTC stamp for rows written by this template) |
| updated_at | string | When workflow 02 wrote the row, local ISO in the workflow timezone |

## health_workouts

Written by workflow 02, read by workflow 03. One row per workout id.

| Column | Type | Notes |
| --- | --- | --- |
| workout_id | string | Upsert key. The workout `id` from Health Auto Export |
| name | string | |
| start_at | string | UTC ISO, or `""` if `start` did not parse |
| end_at | string | UTC ISO, or `""` if `end` did not parse |
| start_local | string | Start, ISO in the workflow timezone |
| date_local | string | Start date `YYYY-MM-DD` in the workflow timezone |
| duration_min | number | Export `duration` is seconds, stored as minutes to one decimal |
| distance_km | number | Metres and miles converted; see the README |
| active_energy_kcal | number | Kilojoules converted by dividing by 4.184 |
| total_energy_kcal | number | Same conversion |
| avg_hr | number | |
| max_hr | number | |
| min_hr | number | |
| source | string | Device or source name |
| location | string | For example `Indoor` or `Outdoor` when the export says so |
| payload_id | string | |
| received_at | string | Payload `received_at` |
| body | string | Full workout JSON, including series, splits, and segments. Not returned by MCP |
| updated_at | string | When workflow 02 wrote the row, local ISO in the workflow timezone |

`get_workout` only accepts ids matching `^[A-Za-z0-9-]{8,64}$`. Dedup still stores other ids; those rows will not come back through that tool until the id matches.

## hae_dedup_state

Read and written by workflow 02. Workflow 01's cleanup reads the watermark row.

| Column | Type | Notes |
| --- | --- | --- |
| name | string | Upsert key. Two rows: `health_raw_watermark` and `health_raw_dedup_lock` |
| value | string | Watermark row: largest processed `health_raw.received_at`, normalized to the UTC stamp. Empty on the lock row |
| note | string | Watermark row: run summary (row, point, and workout counts, plus the first parse errors). Lock row: which execution took or released it |
| updated_at | string | Local ISO in the workflow timezone |
| lock_owner | string | Lock row only. Execution id of the dedup run that holds the lock, or `""` when released |
| lock_until | string | Lock row only. Expiry as the UTC stamp `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`. `1970-01-01T00:00:00.000Z` when released |

`lock_owner` and `lock_until` are new in the Unreleased revision. If your table predates it, add both as `string` columns before you import the new workflow 02. Without them dedup does not run: `Take lock` cannot store the owner, so either that node fails or `We hold the lock` comes out false and the run stops. The watermark row leaves both columns empty.

Workflow 02 creates the lock row on its first run. You do not need to add it by hand. A lock counts as free when `lock_owner` is empty, when `lock_until` is in the past, or when `lock_until` is not that UTC stamp. To clear a stuck lock, set `lock_until` to `1970-01-01T00:00:00.000Z` or delete the lock row. Do not do that while a run is still working. See [setup-n8n.md](setup-n8n.md#dedup-lock).

There is no cleanup workflow for `health_points`, `health_workouts`, or `hae_dedup_state`. The 3-day delete applies only to `health_raw`. A historical export is kept in the flattened tables after the raw body is gone, as long as dedup ran while the raw row still existed.

n8n's default data-table storage cap is 50 MB (`N8N_DATA_TABLES_MAX_SIZE_BYTES` on a self-hosted instance). Workout `body` values are the heavy part.
