# Set up Health Auto Export

[Health Auto Export](https://www.healthyapps.dev/apps/health-auto-export/) is the iOS app that reads Apple Health and POSTs JSON to a URL you choose. These workflows accept that POST. They do not talk to Apple.

Create the n8n side first ([setup-n8n.md](setup-n8n.md)), publish workflow 01, and only then save the automation on the phone. Use the **production** webhook URL.

## Two automations

Create a REST API automation for **metrics** and a separate one for **workouts**. Both can use the same URL, the same header, and the same JSON format. Workflow 01 stores whatever arrives. Workflow 02 splits `data.metrics` and `data.workouts` out of each body.

Do not add an automation for ECG, electrocardiograms, or other Health Auto Export types. The receiver records `ecg` in the `kind` column when that array is present, and then the 3-day cleanup deletes the raw row. Nothing copies ECG samples into `health_points` or `health_workouts`, and the MCP tools cannot return them.

## REST API settings

| Setting | Value |
| --- | --- |
| URL | `https://<your-n8n-host>/webhook/<ingest-path>` |
| Method | POST |
| Header name | The Header Auth name you created, such as `X-HAE-Token` |
| Header value | The same random value stored in the n8n credential. Type it into the app. Do not copy it into a note that gets committed |
| Body format | JSON |
| Batch requests | On |

Batch requests put many samples in one POST. That is what you want. Each POST becomes one `health_raw` row only when the insert succeeds. The webhook returns `{"ok":true}` after that insert, not before it. If the phone treats a non-2xx as a failed export, it can send the batch again. Dedup upserts by `metric|timestamp` and by workout id, so a repeated batch updates the same rows. Within one run, the newest raw row wins. A batched body is often larger than nginx's default 1 MB. See [reverse-proxy.md](reverse-proxy.md).

## Date range

For the first successful sync, keep the range short (a couple of days) so you can see one raw row become a handful of points. After that, let the app continue from its last successful export.

A wide backfill also works, with two limits:

- The raw body is deleted 3 days after it arrives. Dedup has to run in that window. Flattened points and workouts stay after the raw row is gone.
- The data-table storage cap (50 MB unless you raised `N8N_DATA_TABLES_MAX_SIZE_BYTES`) includes full workout JSON. A long workout history with heart-rate series can fill that cap even though MCP only returns 200 series points at a time.

The app's clock does not have to match the n8n workflow timezone. Sample timestamps in the JSON carry their own offset and are converted to UTC. The workflow timezone only decides which local calendar day a sample belongs to, and when the 03:30 cleanup fires.

## What a body looks like

The phone sends this shape. Values below are synthetic. Do not replace them with a real export in this repo.

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
    "workouts": []
  }
}
```

`date` is `yyyy-MM-dd HH:mm:ss ZZZ`. ISO is accepted as a fallback. A metric sample's quantity is `qty`, or `Avg` when that is what heart-rate rows use, or `totalSleep` / `asleep` for sleep. Other fields on the sample are kept in the `extra` column.

These templates were checked with synthetic rows that have `date` and `qty`. They were not checked against every Health Auto Export aggregation mode. A sample that only has `startDate` / `endDate` and no `date` is skipped as a bad date and is not stored as a point. If you turn aggregation off, confirm the JSON still has `date` on each sample before you rely on it. `get_metric_summary` describes heart-rate `qty` as a daily average only when the export's `qty` or `Avg` is that daily value. Raw samples are summarized as themselves.

A workout needs an `id`. `start` and `end` use the same timestamp format. `duration` is seconds. Distance and energy may be objects with `qty` and `units` (`m`, `mi`, `kJ`, `kcal`). A workout body can include a GPS route. That JSON stays in `health_workouts.body`. The MCP tools do not return the column. `get_workout` does return splits, segments, and a short heart-rate series when you ask for it.

A POST whose body is missing, null, or not an object, or whose `data` is not an object, is not stored. The webhook returns 400 `{"ok":false,"error":"missing body.data"}`. On n8n 2.46.4, before this check, that POST was stored with `kind` `unknown` and `body` null and still got 200. `kind` `unknown` now means the body was stored and `data` had no `metrics`, `workouts`, or `ecg` arrays. Rows written by workflow 01 are `JSON.stringify` output, so dedup will not report `bad JSON` for them. A raw row that has no `payload_id` or `body` is counted as `skipped` on the watermark and is not read again. `bad date` means a sample had no parseable `date`.
