# Expose n8n without exposing the editor

The phone and the MCP client need two URLs. Nothing else on the n8n process should be reachable from the internet.

- `https://<your-n8n-host>/webhook/<ingest-path>`
- `https://<your-n8n-host>/mcp/<mcp-path>`

Block the editor, `/rest`, `/healthz` if you do not need it publicly, webhook test URLs, and every other path. A long random path is not a substitute for an allowlist. The paths are still secrets. The allowlist is what stops someone from opening the UI on the same host.

Terminate TLS at the proxy or at Cloudflare. Set n8n's public URL so generated webhook links match that host. On a self-hosted instance that is typically:

- `WEBHOOK_URL=https://<your-n8n-host>/`
- `N8N_HOST`, `N8N_PROTOCOL`, and `N8N_PROXY_HOPS` when TLS stops in front of n8n

Use the values from the n8n hosting docs for your install. The examples below assume n8n listens on `127.0.0.1:5678` and is not reachable on a public interface.

## nginx

Replace the host and the two paths. `proxy_pass` to loopback is the only upstream.

```nginx
server {
    listen 443 ssl;
    server_name health.example.com;

    # Certificate directives stay with your existing TLS setup.

    location / {
        return 404;
    }

    location = /webhook/hae-ingest-YOUR-PATH {
        client_max_body_size 32m;
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /mcp/hae-mcp-YOUR-PATH {
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_buffering off;
        gzip off;
        chunked_transfer_encoding off;
        proxy_set_header Connection '';
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }
}
```

Exact `location =` matches only that path. `/webhook/hae-ingest-YOUR-PATH-extra` does not match. Do not add a prefix location for all of `/webhook/` or `/mcp/`.

nginx's default body limit is 1 MB. A batched Health Auto Export post, especially workouts with a heart-rate series, is often larger, and the phone then gets 413 before n8n runs. `client_max_body_size 32m` is only on the ingest location. n8n 2.46.4 still defaults `N8N_PAYLOAD_SIZE_MAX` to 16 (mebibytes) for the API and webhook payload. A body between 16 and 32 MiB passes nginx and is rejected by n8n. Raise both together if you need a larger batch, and keep the proxy limit at least as high as n8n's.

This template's MCP Server Trigger is typeVersion 2.1. On n8n 2.46.4 that version registers GET, POST, and DELETE on the exact `/mcp/<path>`. It does not register `/sse` or `/messages` (those suffixes are added only when the node version is below 2). The exact MCP location matches this template. It was not tested with a live `mcp-remote` session. If you downgrade the node below version 2, also allow `^~ /mcp/hae-mcp-YOUR-PATH/` (note the trailing slash) so `/sse` and `/messages` match and `/mcp/hae-mcp-YOUR-PATH-extra` does not.

The MCP location turns buffering and gzip off. n8n's MCP Server Trigger docs require that for SSE and streamable HTTP. A buffered proxy stalls or cuts the session. The long read timeout covers an idle MCP connection.

If you run queue mode with more than one webhook replica, send every `/mcp` request to a single replica. Splitting those requests across replicas drops the session. One replica is enough for the webhook ingest path as well, and it avoids the same class of problem.

## Cloudflare Tunnel

A tunnel can apply the same allowlist in ingress rules so the n8n port never has a public listener. `cloudflared` matches rules in order. Put the two paths first and send everything else to a 404.

`path` is a regular expression, and cloudflared does not add `^` and `$` for you. An unanchored `/webhook/hae-ingest-YOUR-PATH` also matches `/webhook/hae-ingest-YOUR-PATH-extra` and any URL that merely contains that string. The examples are anchored. Re-check this if you upgrade cloudflared.

```yaml
ingress:
  - hostname: health.example.com
    path: ^/webhook/hae-ingest-YOUR-PATH$
    service: http://127.0.0.1:5678
  - hostname: health.example.com
    path: ^/mcp/hae-mcp-YOUR-PATH$
    service: http://127.0.0.1:5678
  - service: http_status:404
```

For an MCP node older than version 2, the second path can be `^/mcp/hae-mcp-YOUR-PATH(/.*)?$` so `/sse` and `/messages` match and a longer sibling path still does not.

A hostname proxied by Cloudflare, including many tunnel hostnames, is also subject to the plan upload limit. Cloudflare's 413 documentation lists 100 MB on Free and Pro, 200 MB on Business, and up to 5 GB on Enterprise: [Error 413](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/). Those caps are above n8n's 16 MiB default, so a 413 from Cloudflare means the batch is larger than the plan limit. A 413 from n8n means `N8N_PAYLOAD_SIZE_MAX`.

`health.example.com` is a placeholder. Use a hostname you control, and do not commit a tunnel token. Cloudflare Access in front of these paths is optional. If you add it, the Health Auto Export app and `mcp-remote` must be able to pass that check **and** still send the n8n header or bearer token. A browser login wall in front of the webhook will stop the phone.

Streamable HTTP through the tunnel has the same buffering concern as nginx. If MCP sessions hang after the handshake, disable response buffering on that path in the Cloudflare dashboard (or the equivalent configuration) the same way the nginx example does.

## What not to publish

- The n8n editor port on a public interface "just for setup".
- A catch-all ingress to port 5678.
- The webhook **test** URL. It only works while the editor is listening and it is not the URL the phone should store.
- Logs that print the `X-HAE-Token` or `Authorization` header. Those are the credentials.

After the proxy is up, call the webhook with a wrong token and expect 401 or 403, then with the right token and the synthetic body from [setup-n8n.md](setup-n8n.md) and expect `{"ok":true}`. From outside the network, any other path on that host should be 404.
