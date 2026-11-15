# Connect an MCP client

Workflow 03 is an MCP server with four tools. Publish it first. The URL is the **production** MCP URL:

`https://<your-n8n-host>/mcp/<mcp-path>`

n8n's MCP Server Trigger speaks SSE and streamable HTTP. It does not speak stdio. Clients that only start a local process need a bridge. [mcp-remote](https://www.npmjs.com/package/mcp-remote) is that bridge. The bearer token stays in an environment variable.

The tools:

| Tool | What it returns |
| --- | --- |
| `list_metrics` | Metric name, units, row count, first and last local date, and how many distinct sources. No samples |
| `get_metric_summary` | Count, average, min, max, and sum of `qty` per day, week, or month. Heart rate adds `hr_min` / `hr_max`. Sleep adds average stage hours. At most 92 days for `day`, 366 for `week` or `month` |
| `list_workouts` | Summary columns only, newest first, at most 50 workouts, over at most 366 local dates |
| `get_workout` | One workout: summary, splits, segments, and heart-rate recovery. Recovery at 60s and 120s is null when no sample is at or after that offset. `include_series=true` adds a heart-rate series down to at most 200 points. This is more than an aggregate |

Dates are `YYYY-MM-DD` calendar days in the workflow timezone, inclusive. Call `list_metrics` before `get_metric_summary` so the metric name matches a stored name. Week labels use Luxon `startOf('week')` without `useLocaleWeeks`. On the n8n 2.46.4 test that was the Monday of the ISO week. `list_metrics` ignores an empty `source`.

`list_metrics` loads every `health_points` row. `get_metric_summary` loads every row of that metric and only then applies the 92 or 366 day cap. The cap limits the reply, not the read. A date filter on the Data Table node would drop the "metric exists but not in this range" note, so it is not in the template.

Responses never include `health_raw.body` or `health_workouts.body`. Whatever they do include is sent to the MCP client's model provider.

## Claude Desktop

Claude Desktop's config starts local MCP processes. Point it at `mcp-remote`, and let `mcp-remote` add the Authorization header.

Config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Quit Claude Desktop before editing the file. Merge this into an existing `mcpServers` object instead of replacing the file if you already have other servers.

```json
{
  "mcpServers": {
    "hae-health": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<your-n8n-host>/mcp/<mcp-path>",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <token>"
      }
    }
  }
}
```

Replace the URL. Replace `<token>` with the Bearer Auth credential value. There is no space in `Authorization:${AUTH_HEADER}` because Claude Desktop on Windows, and some other clients, do not escape spaces inside `args`. The space after `Bearer` lives in the environment variable, which is fine.

n8n's own MCP Server Trigger docs show `Authorization: Bearer ${AUTH_TOKEN}` in `args` with only the token in the environment. That form works where the client preserves spaces. Prefer the form above when the client is Claude Desktop or Cursor.

Do not commit this file once the token is filled in. If the config must sit in a repo, keep `AUTH_HEADER` out of it and inject the environment another way. `mcp-remote` documents `--header-file` for the same header, one `Name: value` line per row, so the token is not in the process arguments. The file should be readable only by you.

Fully quit and reopen the client after saving. The four tools show up for a new chat. If the client lists the tools but every call fails, publish workflow 03 again. Tool calls run the published workflow.

## Other clients

Any MCP client that can send a bearer token to a remote HTTP or SSE endpoint can use the same URL and header:

`Authorization: Bearer <token>`

For a stdio-only client, the same `mcp-remote` command line as above is the adapter. Node 18 or newer is required so `npx` can start it.

Cursor and other local clients use the same `mcpServers` shape. Keep the token in `env` (or a header file), not in a committed JSON file.

Clients that already speak remote MCP (a URL field plus a header) do not need `mcp-remote`. Give them the production `/mcp/<path>` URL and the bearer header.

## If the connection drops

- 401 means the bearer token does not match the `HAE MCP Bearer` credential, or the credential is not selected on the MCP node.
- A URL that ends in `/webhook/...` is the ingest path, not the MCP path.
- Behind several webhook replicas, pin every `/mcp` request to one replica. n8n documents this under the MCP Server Trigger limitations. A reverse proxy that fans `/mcp` out across replicas will drop SSE and streamable HTTP sessions.
- nginx in front of `/mcp` must not buffer the response. See [reverse-proxy.md](reverse-proxy.md).
