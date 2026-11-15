# Changelog

## Unreleased

Follow-up after a manual run on n8n 2.46.4 and a review of the initial templates. Commit `82c144d` was re-tested on that version (reply after insert, loop order, idempotent rerun, watermark-aware cleanup, tool cases, Monday weeks, no UTC warning under `Asia/Taipei`). `$workflow.id` with a published workflow 03 and an MCP client is still unverified. The body gate, skipped-row watermark, dedup lock, forward-only watermark, and loop-based min/max below are newer than that re-test.

- Workflow 01 rejects a POST unless `body` and `body.data` are both non-null objects. The false branch returns 400 `{"ok":false,"error":"missing body.data"}` and does not insert. A stored row with `kind` `unknown` means `data` had no metrics, workouts, or ecg arrays.
- Workflow 02 sets the watermark from every row it read, including rows skipped for a missing `payload_id` or `body`, and records `skipped=` in the watermark note and errors.
- Workflow 02 runs one execution at a time. A lock row `health_raw_dedup_lock` in `hae_dedup_state` holds the owner's execution id (`lock_owner`) and an expiry 120 minutes out (`lock_until`, UTC stamp). A run that finds another execution's unexpired lock stops before reading anything. Otherwise it writes the lock, waits 3 seconds, and continues only if it reads its own id back. The Code node's last item is `unlock`. `Release lock` clears the row only while `lock_owner` is still this execution. A run with no new raw rows also releases it: `Read new health_raw rows` always outputs an item, and the Code node ignores that empty item. A failed run holds the lock until it expires. **New columns:** add `lock_owner` and `lock_until` (both `string`) to an existing `hae_dedup_state` before importing. Steps are in [docs/setup-n8n.md](docs/setup-n8n.md#upgrade-add-the-dedup-lock-columns).
- Workflow 02 re-reads the stored watermark after its upserts and saves its own only when it is later (both normalized to the UTC stamp). The watermark never moves backwards. `Save watermark (runs last)` is renamed `Save watermark if newer`.
- Workflow 03 computes min and max with a loop (`minOf` / `maxOf`) instead of spreading arrays into `Math.min` / `Math.max`. That covers `stats()`, the per-period and overall heart-rate `hr_min` / `hr_max`, and the `get_workout` heart-rate series. A dense 92- or 366-day range no longer throws `RangeError`. Results are unchanged, including `null` for a series chunk with a missing `Min` or `Max`.
- `scripts/check_repo.py` checks the lock chain, the forward-only watermark wiring, the owner-filtered release, the unlock item being last, and that no Code node spreads an array into `Math.min` / `Math.max`.

- Webhook replies `{"ok":true}` only after `health_raw` insert succeeds (`responseMode: responseNode`). The body is static and does not echo the stored row.
- Cleanup deletes `received_at` older than the earlier of the 3-day cutoff and a UTC watermark. A missing or non-UTC watermark deletes nothing.
- Dedup walks one output item at a time (Split in Batches v3, batch size 1) and saves the watermark only after earlier upserts return to the loop.
- Data Table nodes are By ID placeholders again. By Name bound `health_raw` to `test_health_raw` on 2.46.4.
- `get_workout` recovery at 60s and 120s is null unless a sample exists at or after that offset.
- `list_metrics` does not count an empty `source`.
- Successful production executions, manual executions, and error executions are not saved (`saveDataErrorExecution` is `none` on all three workflows). Debugging a failure means turning error saving on for that run, then turning it off. Code nodes add `timezone_warning` when the workflow zone is UTC.
- Docs: post-import checklist (timezone, execution order v1, credentials, table ids), nginx `client_max_body_size`, `N8N_PAYLOAD_SIZE_MAX`, Cloudflare upload limits, anchored cloudflared paths, MCP path for node 2.1, privacy (LLM provider, GPS in workout body, `get_workout` series).

## 0.1.0 — 2026-11-14

Initial templates for receiving Health Auto Export posts, deduplicating them into n8n Data Tables, and exposing MCP tools. The bullets below describe that first commit. Unreleased replaces By Name binding and the immediate webhook reply.

These workflows were adapted from a working export. A later manual run on n8n 2.46.4 covered the logic in that commit, not an MCP client, and not the Unreleased edits.

Behaviour changes from that export:

- Workflow 03 tool nodes call this workflow with `={{ $workflow.id }}` instead of a placeholder id.
- Data Table nodes select tables by name (`health_raw`, `health_points`, `health_workouts`, `hae_dedup_state`) instead of by id.
- `received_at` is written, filtered, and stored as a watermark in fixed-width UTC (`yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`). The dedup Code node compares instants in that form, including legacy offset strings it actually reads. See [docs/setup-n8n.md](docs/setup-n8n.md) before upgrading a database that already has offset-form values.
- Sticky notes list the fixed constants (retention, schedules, caps, regexes).
- The `list_metrics` tool description no longer says the dates are Taipei dates. They follow the workflow timezone.
- MCP server instructions and the `list_metrics` result note say "the workflow timezone" (wording only).

Unchanged on purpose: retention of 3 days, cleanup at 03:30, the 30-minute dedup interval, the 2-minute settle delay, MCP caps (92 / 366 days, 50 workouts, 200 heart-rate points), upsert keys, execution order v1, and the template timezone `Asia/Taipei`.
