#!/usr/bin/env python3
"""Parse the workflow JSON, check a few structural invariants, and scan the repo for secrets."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / "workflows" / "01-receiver.json",
    ROOT / "workflows" / "02-dedup.json",
    ROOT / "workflows" / "03-query-mcp.json",
)

TEXT_SUFFIXES = {".md", ".json", ".yml", ".yaml", ".py", ".txt", ""}
SKIP_DIRS = {".git"}

SECRET_PATTERNS = (
    ("email address", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("aws access key id", re.compile("AKIA" + r"[0-9A-Z]{16}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("github token", re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("api key prefix", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")),
    ("bearer token literal", re.compile(r"Bearer\s+[A-Za-z0-9_\-]{24,}")),
    ("long hex token", re.compile(r"\b[a-f0-9]{40,}\b")),
)

ALLOWED_HOST_SUFFIXES = (
    "example.com",
    "n8n.io",
    "github.com",
    "npmjs.com",
    "cloudflare.com",
    "healthyapps.dev",
)
ALLOWED_HTTP_HOSTS = {"127.0.0.1", "localhost"}

URL_RE = re.compile(r"https?://([^/\s\"'`<>]+)")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_workflow(path: Path, errors: list[str]) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f"{path.relative_to(ROOT)}: invalid JSON ({exc})")
        return None
    if not isinstance(data, dict):
        fail(errors, f"{path.name}: top-level JSON value must be an object")
        return None
    for key in ("name", "nodes", "connections", "settings"):
        if key not in data:
            fail(errors, f"{path.name}: missing {key}")
    if data.get("active") is not False:
        fail(errors, f"{path.name}: active must be false so import does not publish a placeholder path")
    settings = data.get("settings") or {}
    if settings.get("executionOrder") != "v1":
        fail(errors, f"{path.name}: settings.executionOrder must stay v1")
    if settings.get("timezone") != "Asia/Taipei":
        fail(errors, f"{path.name}: template timezone must stay Asia/Taipei")
    if settings.get("saveDataSuccessExecution") != "none":
        fail(errors, f"{path.name}: saveDataSuccessExecution must be none")
    if settings.get("saveManualExecutions") is not False:
        fail(errors, f"{path.name}: saveManualExecutions must be false")
    if settings.get("saveDataErrorExecution") != "none":
        fail(errors, f"{path.name}: saveDataErrorExecution must be none")
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        fail(errors, f"{path.name}: nodes must be a non-empty list")
        return data
    for node in nodes:
        creds = node.get("credentials") or {}
        if not isinstance(creds, dict):
            fail(errors, f"{path.name}: {node.get('name')} credentials must be an object")
            continue
        for cred_type, cred in creds.items():
            if not isinstance(cred, dict) or set(cred) != {"name"}:
                fail(errors, f"{path.name}: {node.get('name')} credential {cred_type} must be name-only, got {cred!r}")
    return data


def check_workflows(errors: list[str]) -> None:
    loaded = []
    for path in WORKFLOWS:
        if not path.is_file():
            fail(errors, f"missing {path.relative_to(ROOT)}")
            continue
        data = load_workflow(path, errors)
        if data is not None:
            loaded.append((path, data))
    by_name = {path.name: data for path, data in loaded}

    receiver = by_name.get("01-receiver.json")
    dedup = by_name.get("02-dedup.json")
    query = by_name.get("03-query-mcp.json")
    if receiver is None or dedup is None or query is None:
        return

    def node(data: dict, filename: str, name: str) -> dict:
        found = [item for item in data["nodes"] if item.get("name") == name]
        if len(found) != 1:
            fail(errors, f"{filename}: expected one node named {name!r}, found {len(found)}")
            return {}
        return found[0]

    def targets(data: dict, name: str, output_index: int) -> list[str]:
        branches = (data.get("connections") or {}).get(name, {}).get("main") or []
        if output_index >= len(branches):
            return []
        return [item.get("node") for item in branches[output_index]]

    def table_id(node_obj: dict, filename: str, expected_name: str, placeholder: str) -> None:
        loc = (node_obj.get("parameters") or {}).get("dataTableId") or {}
        if loc.get("mode") != "id" or loc.get("value") != placeholder or loc.get("cachedResultName") != expected_name:
            fail(errors, f"{filename}: {node_obj.get('name')} must be id {placeholder} ({expected_name}), got {loc!r}")

    def condition_at(node_obj: dict, index: int) -> dict:
        conditions = ((node_obj.get("parameters") or {}).get("filters") or {}).get("conditions") or []
        if index >= len(conditions) or not isinstance(conditions[index], dict):
            fail(errors, f"{node_obj.get('name')}: missing filter condition {index}")
            return {}
        return conditions[index]

    utc_now = "={{ $now.toUTC().toFormat(\"yyyy-MM-dd'T'HH:mm:ss.SSS'Z'\") }}"
    store = node(receiver, "01-receiver.json", "Store in health_raw")
    table_id(store, "01-receiver.json", "health_raw", "__HEALTH_RAW_TABLE_ID__")
    received = ((store.get("parameters") or {}).get("columns") or {}).get("value", {}).get("received_at")
    if received != utc_now:
        fail(errors, "Store in health_raw received_at must be the fixed-width UTC expression")
    webhook = node(receiver, "01-receiver.json", "HAE Webhook")
    if (webhook.get("parameters") or {}).get("path") != "hae-ingest-CHANGE-ME":
        fail(errors, "webhook path placeholder was replaced")
    if (webhook.get("parameters") or {}).get("responseMode") != "responseNode":
        fail(errors, "webhook must respond with the Respond to Webhook node, after the insert")
    if targets(receiver, "HAE Webhook", 0) != ["Has body data"]:
        fail(errors, "webhook must connect to Has body data")
    if targets(receiver, "Has body data", 0) != ["Store in health_raw"]:
        fail(errors, "a valid body must be stored")
    if targets(receiver, "Has body data", 1) != ["Reply missing body"]:
        fail(errors, "a missing body must not be stored")
    if targets(receiver, "Store in health_raw", 0) != ["Reply ok"]:
        fail(errors, "insert must connect to Reply ok")
    gate = node(receiver, "01-receiver.json", "Has body data")
    if gate.get("type") != "n8n-nodes-base.if":
        fail(errors, "Has body data must be an IF node")
    if "body.data" not in json.dumps(gate.get("parameters")):
        fail(errors, "Has body data must require body.data")
    reply = node(receiver, "01-receiver.json", "Reply ok")
    if reply.get("type") != "n8n-nodes-base.respondToWebhook":
        fail(errors, "Reply ok must be a Respond to Webhook node")
    if "ok" not in str((reply.get("parameters") or {}).get("responseBody")):
        fail(errors, "Reply ok must return an ok body")
    missing = node(receiver, "01-receiver.json", "Reply missing body")
    if missing.get("type") != "n8n-nodes-base.respondToWebhook":
        fail(errors, "Reply missing body must be a Respond to Webhook node")
    missing_params = missing.get("parameters") or {}
    if missing_params.get("options", {}).get("responseCode") != 400:
        fail(errors, "Reply missing body must use HTTP 400")
    if "missing body.data" not in str(missing_params.get("responseBody")):
        fail(errors, "Reply missing body must name the error missing body.data")
    cleanup = node(receiver, "01-receiver.json", "Delete rows older than 3 days")
    table_id(cleanup, "01-receiver.json", "health_raw", "__HEALTH_RAW_TABLE_ID__")
    if condition_at(cleanup, 0).get("keyValue") != "={{ $json.delete_before }}":
        fail(errors, "cleanup must delete where received_at is before the computed cutoff")
    table_id(node(receiver, "01-receiver.json", "Read cleanup watermark"), "01-receiver.json", "hae_dedup_state", "__HAE_DEDUP_STATE_TABLE_ID__")
    cutoff = (node(receiver, "01-receiver.json", "Retention cutoff").get("parameters") or {}).get("jsCode", "")
    if "days: 3" not in cutoff or "delete_before" not in cutoff or "watermark_ignored" not in cutoff:
        fail(errors, "retention cutoff must keep the 3-day window and ignore a non-UTC watermark")
    check_code_syntax(errors, "01-receiver.json Retention cutoff", cutoff)
    if targets(receiver, "Daily cleanup 03:30", 0) != ["Read cleanup watermark"]:
        fail(errors, "cleanup schedule must read the watermark first")
    if targets(receiver, "Retention cutoff", 0) != ["Delete rows older than 3 days"]:
        fail(errors, "retention cutoff must feed the delete node")
    schedule = node(receiver, "01-receiver.json", "Daily cleanup 03:30")
    interval = (((schedule.get("parameters") or {}).get("rule") or {}).get("interval") or [{}])[0]
    if not isinstance(interval, dict) or interval.get("triggerAtHour") != 3 or interval.get("triggerAtMinute") != 30:
        fail(errors, "cleanup schedule must stay 03:30")

    every = node(dedup, "02-dedup.json", "Every 30 minutes")
    every_rules = ((every.get("parameters") or {}).get("rule") or {}).get("interval") or []
    every_interval = every_rules[0] if every_rules else {}
    if every_interval.get("minutesInterval") != 30:
        fail(errors, "dedup interval must stay 30 minutes")
    if (every.get("typeVersion") != 1.2):
        fail(errors, "30-minute schedule must stay Schedule Trigger 1.2")
    read_raw = node(dedup, "02-dedup.json", "Read new health_raw rows")
    table_id(read_raw, "02-dedup.json", "health_raw", "__HEALTH_RAW_TABLE_ID__")
    upper = condition_at(read_raw, 1).get("keyValue", "")
    if "minus({ minutes: 2 })" not in upper or "toUTC()" not in upper:
        fail(errors, "settle delay must stay 2 minutes and use UTC")
    for state_node in (
        "Read lock",
        "Take lock",
        "Confirm lock",
        "Read watermark",
        "Re-read watermark",
        "Save watermark if newer",
        "Release lock",
    ):
        table_id(node(dedup, "02-dedup.json", state_node), "02-dedup.json", "hae_dedup_state", "__HAE_DEDUP_STATE_TABLE_ID__")
    lock_chain = (
        "Every 30 minutes",
        "Read lock",
        "Check lock is free",
        "Take lock",
        "Settle 3 seconds",
        "Confirm lock",
        "We hold the lock",
        "Read watermark",
    )
    for src, dst in zip(lock_chain, lock_chain[1:]):
        if targets(dedup, src, 0) != [dst]:
            fail(errors, f"dedup lock chain: {src} must connect to {dst}")
    if targets(dedup, "We hold the lock", 1) != []:
        fail(errors, "dedup must stop when another execution holds the lock")
    for always in ("Read lock", "Re-read watermark", "Read new health_raw rows"):
        if node(dedup, "02-dedup.json", always).get("alwaysOutputData") is not True:
            fail(errors, f"{always} must always output an item so the run reaches the unlock item")
    lock_code = (node(dedup, "02-dedup.json", "Check lock is free").get("parameters") or {}).get("jsCode", "")
    if "const LOCK_MINUTES = 120" not in lock_code or "health_raw_dedup_lock" not in lock_code or "$execution.id" not in lock_code:
        fail(errors, "lock check must use the health_raw_dedup_lock row, the execution id, and a 120-minute expiry")
    check_code_syntax(errors, "02-dedup.json Check lock is free", lock_code)
    take = (node(dedup, "02-dedup.json", "Take lock").get("parameters") or {})
    take_cols = (take.get("columns") or {}).get("value") or {}
    if take.get("operation") != "upsert" or "lock_owner" not in take_cols or "lock_until" not in take_cols:
        fail(errors, "Take lock must upsert lock_owner and lock_until")
    hold = json.dumps(node(dedup, "02-dedup.json", "We hold the lock").get("parameters"))
    if "lock_owner" not in hold or "$execution.id" not in hold:
        fail(errors, "We hold the lock must compare lock_owner with the execution id")
    release = node(dedup, "02-dedup.json", "Release lock").get("parameters") or {}
    release_conditions = {
        (c.get("keyName"), c.get("keyValue"))
        for c in ((release.get("filters") or {}).get("conditions") or [])
        if isinstance(c, dict) and c.get("condition", "eq") == "eq"
    }
    owned = ("name", "health_raw_dedup_lock") in release_conditions and any(
        key == "lock_owner" and "$execution.id" in str(value) for key, value in release_conditions
    )
    if release.get("operation") != "update" or release.get("matchType") != "allConditions" or not owned:
        fail(errors, "Release lock must update only the row still owned by this execution")
    if ((release.get("columns") or {}).get("value") or {}).get("lock_until") != "1970-01-01T00:00:00.000Z":
        fail(errors, "Release lock must set lock_until to the epoch UTC stamp")
    compare = (node(dedup, "02-dedup.json", "Compare with stored watermark").get("parameters") or {}).get("jsCode", "")
    if "next > stored" not in compare or "$('Flatten metrics and workouts').all()" not in compare:
        fail(errors, "watermark compare must save only a later value than the stored one")
    check_code_syntax(errors, "02-dedup.json Compare with stored watermark", compare)
    table_id(node(dedup, "02-dedup.json", "Upsert health_points"), "02-dedup.json", "health_points", "__HEALTH_POINTS_TABLE_ID__")
    table_id(node(dedup, "02-dedup.json", "Upsert health_workouts"), "02-dedup.json", "health_workouts", "__HEALTH_WORKOUTS_TABLE_ID__")
    loop = node(dedup, "02-dedup.json", "One record at a time")
    if loop.get("typeVersion") != 3 or (loop.get("parameters") or {}).get("batchSize") != 1:
        fail(errors, "dedup must walk records one at a time with Split in Batches v3")
    if targets(dedup, "Flatten metrics and workouts", 0) != ["One record at a time"]:
        fail(errors, "flatten must feed the loop")
    if targets(dedup, "One record at a time", 0) != []:
        fail(errors, "Split in Batches v3 output 0 is done and must stay unused")
    if targets(dedup, "One record at a time", 1) != ["Route by record type"]:
        fail(errors, "Split in Batches v3 output 1 must be the per-item loop")
    if targets(dedup, "Route by record type", 0) != ["Upsert health_points"]:
        fail(errors, "point items must upsert health_points")
    if targets(dedup, "Route by record type", 1) != ["Upsert health_workouts"]:
        fail(errors, "workout items must upsert health_workouts")
    if targets(dedup, "Route by record type", 2) != ["Re-read watermark"]:
        fail(errors, "watermark item must re-read the stored watermark first")
    if targets(dedup, "Route by record type", 3) != ["Release lock"]:
        fail(errors, "unlock item must reach the release node")
    if targets(dedup, "Re-read watermark", 0) != ["Compare with stored watermark"]:
        fail(errors, "re-read watermark must feed the compare node")
    if targets(dedup, "Compare with stored watermark", 0) != ["Watermark is newer"]:
        fail(errors, "compare must feed Watermark is newer")
    if targets(dedup, "Watermark is newer", 0) != ["Save watermark if newer"]:
        fail(errors, "a later watermark must be saved")
    if targets(dedup, "Watermark is newer", 1) != ["One record at a time"]:
        fail(errors, "an older watermark must skip the save and return to the loop")
    for back in ("Upsert health_points", "Upsert health_workouts", "Save watermark if newer", "Release lock"):
        if targets(dedup, back, 0) != ["One record at a time"]:
            fail(errors, f"{back} must return to the loop so the watermark cannot run early")
    flatten = (node(dedup, "02-dedup.json", "Flatten metrics and workouts").get("parameters") or {}).get("jsCode", "")
    if "function utcStamp" not in flatten or "health_raw_watermark" not in flatten:
        fail(errors, "dedup code must normalize the watermark to UTC")
    if "const readRows" not in flatten or "skipped=" not in flatten or "r.payload_id && r.body" not in flatten:
        fail(errors, "dedup watermark must cover skipped rows and record the skipped count")
    if flatten.find("let maxReceived") > flatten.find("r.payload_id && r.body"):
        fail(errors, "maxReceived must be computed before rows without payload_id or body are dropped")
    if "Object.keys(r).length > 0" not in flatten:
        fail(errors, "dedup code must ignore the empty item from an empty raw read")
    unlock_at = flatten.find("record_type: 'unlock'")
    if unlock_at < 0 or unlock_at < flatten.find("record_type: 'watermark'") or unlock_at < flatten.rfind("out.push({ json: v })"):
        fail(errors, "dedup code must emit the unlock item last")
    check_code_syntax(errors, "02-dedup.json Flatten metrics and workouts", flatten)

    mcp = node(query, "03-query-mcp.json", "HAE Health MCP Server")
    if (mcp.get("parameters") or {}).get("path") != "hae-mcp-CHANGE-ME":
        fail(errors, "MCP path placeholder was replaced")
    for tool_name in ("list_metrics", "get_metric_summary", "list_workouts", "get_workout"):
        tool = node(query, "03-query-mcp.json", tool_name)
        loc = (tool.get("parameters") or {}).get("workflowId") or {}
        if loc.get("mode") != "id" or loc.get("value") != "={{ $workflow.id }}":
            fail(errors, f"{tool_name}: workflowId must be the $workflow.id expression")
    if "Taipei" in ((node(query, "03-query-mcp.json", "list_metrics").get("parameters") or {}).get("description") or ""):
        fail(errors, "list_metrics description must not hardcode Taipei")
    table_id(node(query, "03-query-mcp.json", "Read all health_points"), "03-query-mcp.json", "health_points", "__HEALTH_POINTS_TABLE_ID__")
    table_id(node(query, "03-query-mcp.json", "Read one metric"), "03-query-mcp.json", "health_points", "__HEALTH_POINTS_TABLE_ID__")
    table_id(node(query, "03-query-mcp.json", "Read workouts in range"), "03-query-mcp.json", "health_workouts", "__HEALTH_WORKOUTS_TABLE_ID__")
    table_id(node(query, "03-query-mcp.json", "Read one workout"), "03-query-mcp.json", "health_workouts", "__HEALTH_WORKOUTS_TABLE_ID__")
    metrics = (node(query, "03-query-mcp.json", "Aggregate list_metrics").get("parameters") or {}).get("jsCode", "")
    summary = (node(query, "03-query-mcp.json", "Aggregate get_metric_summary").get("parameters") or {}).get("jsCode", "")
    workouts = (node(query, "03-query-mcp.json", "Aggregate list_workouts").get("parameters") or {}).get("jsCode", "")
    one = (node(query, "03-query-mcp.json", "Aggregate get_workout").get("parameters") or {}).get("jsCode", "")
    if "if (r.source)" not in metrics or "m.sources[r.source || '']" in metrics:
        fail(errors, "list_metrics must skip empty source names")
    if "92" not in summary or "366" not in summary:
        fail(errors, "metric summary caps 92 and 366 must stay")
    if "const CAP = 50" not in workouts or "366" not in workouts:
        fail(errors, "list_workouts caps must stay 366 days and 50 rows")
    if "const MAX = 200" not in one:
        fail(errors, "heart-rate series cap must stay 200")
    if "seconds <= sec" in one or "if (offset < sec) continue" not in one:
        fail(errors, "heart-rate recovery must ignore samples before the requested offset")
    for label, code in (
        ("Aggregate list_metrics", metrics),
        ("Aggregate get_metric_summary", summary),
        ("Aggregate list_workouts", workouts),
        ("Aggregate get_workout", one),
    ):
        check_code_syntax(errors, label, code)

    spread_extrema = re.compile(r"Math\.(?:min|max)\(\s*\.\.\.")
    for path, data in loaded:
        for item in data.get("nodes") or []:
            loc = (item.get("parameters") or {}).get("dataTableId")
            if isinstance(loc, dict) and loc.get("mode") == "name":
                fail(errors, f"{path.name}: {item.get('name')} still uses data table by name")
            js = (item.get("parameters") or {}).get("jsCode") or ""
            if spread_extrema.search(js):
                fail(errors, f"{path.name}: {item.get('name')} spreads an array into Math.min/Math.max (RangeError on large arrays)")


def check_code_syntax(errors: list[str], label: str, code: str) -> None:
    if not code:
        fail(errors, f"{label}: empty Code node")
        return
    wrapped = "function _n8nCodeNode() {\n" + code + "\n}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(wrapped)
        path = handle.name
    result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if result.returncode != 0:
        fail(errors, f"{label}: JavaScript syntax error\n{result.stderr.strip()}")


def iter_text_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", "Dockerfile"}:
            continue
        found.append(path)
    return found


def host_allowed(host: str) -> bool:
    hostname = host.split("@")[-1].split(":")[0].strip(".").lower()
    if hostname in ALLOWED_HTTP_HOSTS:
        return True
    return any(hostname == suffix or hostname.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def scan_secrets(errors: list[str]) -> None:
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT)
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                fail(errors, f"{rel}: looks like a {label} ({match.group(0)[:48]})")
        for match in URL_RE.finditer(text):
            host = match.group(1)
            if not host_allowed(host):
                fail(errors, f"{rel}: URL host is not on the allowlist ({host})")


def self_test_patterns() -> None:
    samples = {
        "email address": "person" + "@" + "example.com",
        "aws access key id": "AKIA" + "IOSFODNN7EXAMPLE",
        "private key block": "-----BEGIN " + "PRIVATE KEY-----",
        "github token": "ghp_" + ("a" * 20),
        "slack token": "xoxb-" + ("a" * 10),
        "api key prefix": "sk-" + ("a" * 20),
        "jwt": "eyJ" + ("a" * 8) + "." + ("b" * 8) + ".",
        "bearer token literal": "Bearer " + ("a" * 24),
        "long hex token": "ab" * 20,
    }
    for label, pattern in SECRET_PATTERNS:
        if not pattern.search(samples[label]):
            raise SystemExit(f"secret pattern {label} does not match its sample")
    safe = "Authorization: Bearer ${AUTH_TOKEN}\nhttps://docs.n8n.io/changelog/\nhttp://127.0.0.1:5678"
    for _label, pattern in SECRET_PATTERNS:
        if pattern.search(safe):
            raise SystemExit(f"secret pattern matched documentation-safe text: {safe}")


def main() -> int:
    self_test_patterns()
    errors: list[str] = []
    check_workflows(errors)
    scan_secrets(errors)
    if errors:
        print("check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"ok: {len(WORKFLOWS)} workflows parsed, code syntax checked, no secret patterns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
