#!/usr/bin/env python3
"""Python agent. Calls deepwikimcp through the AI Gateway.

The browser page is http://localhost:7777. This process listens on port 9999.
GET /run reuses a saved gateway token. When that token is missing or expired,
it refreshes it, and signs in through the gateway only when refresh cannot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env(Path(__file__).with_name(".env"))

AGENT_NAME = os.environ.get("AIS_AGENT_NAME", "PythonAgent")
CLIENT_ID = os.environ["AIS_CLIENT_ID"]
REDIRECT_URI = os.environ.get("AIS_REDIRECT_URI", "http://localhost:9999/callback")
FRONTEND = os.environ.get("AIS_FRONTEND_URL", "http://localhost:7777").rstrip("/")
GATEWAY = os.environ.get("AIS_GATEWAY", "https://mcp-aigw.portkey.ai").rstrip("/")
DEEPWIKI_SLUG = os.environ.get("AIS_DEEPWIKI_SLUG", "deepwikimcp")
DEEPWIKI_URL = f"{GATEWAY}/{DEEPWIKI_SLUG}/mcp"
PROTOCOL_VERSION = "2025-03-26"

PENDING: dict[str, dict] = {}
PENDING_LOCK = threading.Lock()
RESULTS: dict[str, dict] = {}
RESULTS_LOCK = threading.Lock()
SAVED: dict[str, object] = {}
SAVED_LOCK = threading.Lock()
TOKEN_SKEW_SECONDS = 60
FLOW: dict[str, object] = {"phase": "idle", "steps": []}
FLOW_LOCK = threading.Lock()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def redact(value: str) -> str:
    if len(value) < 12:
        return "(redacted)"
    return value[:4] + "…" + value[-4:]


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    hidden = {"authorization", "x-portkey-api-key", "client_secret"}
    cleaned = {}
    for key, value in headers.items():
        if value.lower().startswith("bearer ") and (key.lower() in hidden or key.lower() == "proxy-authorization"):
            cleaned[key] = "Bearer " + redact(value[7:])
        elif key.lower() in hidden or key.lower() == "proxy-authorization":
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned


def parse_body(raw: bytes) -> object:
    text = raw.decode(errors="replace")
    data_line = next((line[5:].strip() for line in text.splitlines() if line.startswith("data:")), "")
    if data_line:
        try:
            return {"sse": json.loads(data_line), "raw": text[:4000]}
        except json.JSONDecodeError:
            return {"raw": text[:4000]}
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"raw": text[:4000]}


def logged_response_headers(headers: dict[str, str]) -> dict[str, str]:
    kept = {}
    for key, value in headers.items():
        name = key.lower()
        if name in {"content-type", "www-authenticate", "mcp-session-id"} or any(
            part in name for part in ("auth", "mcp-", "portkey", "request-id", "trace")
        ):
            kept[key] = value
    return kept


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes, float, dict[str, str]]:
    encoded = None
    req_headers = {"User-Agent": "ais-mcp-test/1.0", "Accept": "application/json, text/event-stream"}
    if headers:
        req_headers.update(headers)
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode()
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        encoded = body
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        default_port = 443 if parsed.scheme == "https" else 80
        if parsed.port != default_port:
            host = f"{host}:{parsed.port}"
    req_headers["Host"] = host
    if encoded is not None:
        req_headers["Content-Length"] = str(len(encoded))
    request = urllib.request.Request(url, data=encoded, headers=req_headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            elapsed = (time.perf_counter() - started) * 1000
            return response.status, dict(response.headers), response.read(), elapsed, req_headers
    except urllib.error.HTTPError as error:
        elapsed = (time.perf_counter() - started) * 1000
        return error.code, dict(error.headers), error.read(), elapsed, req_headers


def log_entry(step: str, method: str, url: str, request_headers: dict[str, str], request_body: object, status: int, response_headers: dict[str, str], response_body: bytes, elapsed_ms: float) -> dict:
    return {
        "step": step,
        "elapsed_ms": round(elapsed_ms, 1),
        "request": {
            "method": method,
            "url": url,
            "headers": redact_headers(request_headers),
            "body": request_body,
        },
        "response": {
            "status": status,
            "headers": logged_response_headers(response_headers),
            "body": parse_body(response_body),
        },
    }


def initialize_message(client_name: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1.0.0"},
        },
    }


def logged_mcp_call(step: str, header_name: str, header_value: str, message: dict, url: str, extra_headers: dict[str, str] | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "ais-mcp-test/1.0",
        header_name: f"Bearer {header_value}",
    }
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(message).encode()
    status, response_headers, response_body, elapsed, sent_headers = http_request(
        url,
        method="POST",
        headers=headers,
        body=body,
    )
    return log_entry(step, "POST", url, sent_headers, message, status, response_headers, response_body, elapsed)


def call_failed(entry: dict) -> bool:
    response = entry["response"]
    if response["status"] >= 400 or response["status"] == 0:
        return True
    body = response["body"]
    if not isinstance(body, dict):
        return False
    if "error" in body and "sse" not in body:
        return True
    sse = body.get("sse")
    return isinstance(sse, dict) and "error" in sse


def carried_headers(entry: dict, extra_headers: dict[str, str] | None) -> dict[str, str]:
    carried = dict(extra_headers or {})
    for key, value in (entry.get("response", {}).get("headers") or {}).items():
        if key.lower() == "mcp-session-id" and value:
            carried["Mcp-Session-Id"] = value
    return carried


def sse_result(entry: dict) -> dict:
    body = entry["response"]["body"]
    if not isinstance(body, dict):
        return {}
    sse = body.get("sse")
    if isinstance(sse, dict):
        return sse.get("result") if isinstance(sse.get("result"), dict) else {}
    return {}


def tool_arguments(tool: dict) -> dict | None:
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    samples = {
        "repoName": "facebook/react",
        "repo": "facebook/react",
        "repository": "facebook/react",
        "question": "What is the purpose of this repository?",
        "query": "What is the purpose of this repository?",
    }
    arguments = {}
    for name in required:
        if name not in samples:
            return None
        arguments[name] = samples[name]
    if not required and properties:
        for name in properties:
            if name in samples:
                arguments[name] = samples[name]
                break
    return arguments


def listed_tool_logs(credential: str, url: str) -> list[dict]:
    logs = [logged_mcp_call(f"{DEEPWIKI_SLUG} initialize", "Authorization", credential, initialize_message(AGENT_NAME), url)]
    if call_failed(logs[0]):
        return logs
    carried = carried_headers(logs[0], None)
    listed = logged_mcp_call(
        f"{DEEPWIKI_SLUG} tools/list",
        "Authorization",
        credential,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        url,
        carried,
    )
    logs.append(listed)
    if call_failed(listed):
        return logs
    tools = sse_result(listed).get("tools")
    if not isinstance(tools, list):
        return logs
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        arguments = tool_arguments(tool)
        if arguments is None:
            continue
        logs.append(
            logged_mcp_call(
                f"{DEEPWIKI_SLUG} {tool['name']}",
                "Authorization",
                credential,
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool["name"], "arguments": arguments}},
                url,
                carried,
            )
        )
        break
    return logs


def jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(segment))
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def public_params(params: dict[str, list[str]]) -> dict[str, str]:
    hidden = {"code", "state", "session_state", "id_token", "access_token", "refresh_token"}
    shown = {}
    for key, values in params.items():
        value = values[0] if values else ""
        if key.lower() in hidden or "token" in key.lower() or "secret" in key.lower():
            shown[key] = redact(value) if value else ""
        else:
            shown[key] = value
    return shown


def claim_view(payload: dict) -> dict:
    shown = {}
    for key, value in payload.items():
        if isinstance(value, str) and len(value) > 120 and "@" not in value and not value.startswith("http"):
            shown[key] = redact(value)
        else:
            shown[key] = value
    return shown


def idira_return_log(params: dict[str, list[str]], token: dict | None, token_body: object) -> dict:
    claims: dict[str, object] = {}
    fields: dict[str, object] = {}
    if isinstance(token_body, dict):
        for key, value in token_body.items():
            if key in {"access_token", "refresh_token", "id_token"}:
                continue
            fields[key] = value
    if isinstance(token, dict):
        for field in ("id_token", "access_token"):
            raw = str(token.get(field, ""))
            if not raw:
                claims[field] = "absent"
                continue
            payload = jwt_payload(raw)
            claims[field] = claim_view(payload) if payload else "opaque"
    callback = public_params(params)
    error = callback.get("error_description") or callback.get("error") or ""
    token_error = ""
    if isinstance(token_body, dict):
        token_error = str(token_body.get("error_description") or token_body.get("error") or "")
    return {
        "step": "Idira return",
        "elapsed_ms": 0,
        "request": {"method": "GET", "url": REDIRECT_URI, "headers": {}, "body": {}},
        "response": {
            "status": 400 if error or token_error else 200,
            "headers": {},
            "body": {
                "called": True,
                "callback_query": callback,
                "token_fields": fields,
                "claims": claims,
                "detail": "Idira redirects to the gateway, which then redirects here. callback_query is what arrived. claims are decoded from the tokens the gateway issued after that redirect.",
            },
        },
    }


def header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def www_param(header: str, name: str) -> str:
    if not header:
        return ""
    marker = name + "="
    index = header.lower().find(marker.lower())
    if index < 0:
        return ""
    value = header[index + len(marker):].lstrip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 1 else ""
    for separator in (" ", ",", ";"):
        cut = value.find(separator)
        if cut >= 0:
            value = value[:cut]
    return value


def origin_of(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def protected_resource_metadata_url(mcp_url: str) -> str:
    parsed = urllib.parse.urlsplit(mcp_url)
    path = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    return f"{origin_of(mcp_url)}/.well-known/oauth-protected-resource{path}"


def authorization_metadata_urls(issuer: str) -> list[str]:
    parsed = urllib.parse.urlsplit(issuer)
    origin = origin_of(issuer)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        return [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{origin}{path}/.well-known/openid-configuration",
    ]


def json_dict(entry: dict) -> dict:
    body = entry["response"]["body"]
    if isinstance(body, dict) and "raw" not in body and "sse" not in body:
        return body
    return {}


def fetch_json(step: str, url: str) -> dict:
    status, response_headers, response_body, elapsed, sent_headers = http_request(
        url,
        headers={"Accept": "application/json", "MCP-Protocol-Version": PROTOCOL_VERSION},
    )
    return log_entry(step, "GET", url, sent_headers, {}, status, response_headers, response_body, elapsed)


def discover_oauth(mcp_url: str) -> tuple[list[dict], dict | None]:
    logs: list[dict] = []
    message = initialize_message(AGENT_NAME)
    status, response_headers, response_body, elapsed, sent_headers = http_request(
        mcp_url,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        body=json.dumps(message).encode(),
    )
    logs.append(log_entry("discover MCP", "POST", mcp_url, sent_headers, message, status, response_headers, response_body, elapsed))
    resource_meta_url = www_param(header_value(response_headers, "WWW-Authenticate"), "resource_metadata")
    resource_log = fetch_json("protected resource metadata", resource_meta_url or protected_resource_metadata_url(mcp_url))
    logs.append(resource_log)
    resource_doc = json_dict(resource_log)
    if resource_log["response"]["status"] >= 400 or not resource_doc.get("resource"):
        return logs, None
    servers = resource_doc.get("authorization_servers")
    issuer = servers[0] if isinstance(servers, list) and servers else origin_of(mcp_url) + "/"
    auth_doc: dict = {}
    for candidate in authorization_metadata_urls(str(issuer)):
        auth_log = fetch_json("authorization server metadata", candidate)
        logs.append(auth_log)
        parsed = json_dict(auth_log)
        if auth_log["response"]["status"] < 400 and parsed.get("authorization_endpoint") and parsed.get("token_endpoint"):
            auth_doc = parsed
            break
    if not auth_doc:
        return logs, None
    scopes = resource_doc.get("scopes_supported")
    scope = " ".join(str(item) for item in scopes) if isinstance(scopes, list) else ""
    return logs, {
        "authorization_endpoint": str(auth_doc["authorization_endpoint"]),
        "token_endpoint": str(auth_doc["token_endpoint"]),
        "resource": str(resource_doc["resource"]),
        "scope": scope,
        "mcp_url": mcp_url,
    }


def authorize_url(endpoint: str, state: str, challenge: str, resource: str, scope: str) -> str:
    params = [
        ("response_type", "code"),
        ("client_id", CLIENT_ID),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("redirect_uri", REDIRECT_URI),
        ("state", state),
    ]
    if scope:
        params.append(("scope", scope))
    if resource:
        params.append(("resource", resource))
    return endpoint + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, verifier: str, resource: str, token_endpoint: str) -> tuple[dict | None, dict]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "resource": resource,
    }
    safe_form = dict(form)
    safe_form["code"] = redact(code)
    safe_form["code_verifier"] = redact(verifier)
    status, response_headers, response_body, elapsed, sent_headers = http_request(
        token_endpoint,
        method="POST",
        data=form,
    )
    parsed = parse_body(response_body)
    if isinstance(parsed, dict) and "access_token" in parsed:
        shown = dict(parsed)
        shown["access_token"] = redact(str(parsed["access_token"]))
        if "refresh_token" in shown:
            shown["refresh_token"] = redact(str(shown["refresh_token"]))
        if "id_token" in shown:
            shown["id_token"] = redact(str(shown["id_token"]))
    else:
        shown = parsed
    entry = log_entry(
        "token exchange",
        "POST",
        token_endpoint,
        sent_headers,
        safe_form,
        status,
        response_headers,
        b"",
        elapsed,
    )
    entry["response"]["body"] = shown
    token = parsed if isinstance(parsed, dict) and status < 400 and "access_token" in parsed else None
    return token, entry


def remember_token(token: dict, resource: str, token_endpoint: str, mcp_url: str) -> None:
    access = str(token.get("access_token") or "")
    if not access:
        return
    try:
        expires_in = int(token.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    with SAVED_LOCK:
        refresh = str(token.get("refresh_token") or SAVED.get("refresh_token") or "")
        SAVED.clear()
        SAVED.update(
            {
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": time.time() + max(expires_in, 0),
                "resource": resource,
                "token_endpoint": token_endpoint,
                "mcp_url": mcp_url or DEEPWIKI_URL,
            }
        )


def clear_saved_token() -> None:
    with SAVED_LOCK:
        SAVED.clear()


def current_access_token() -> tuple[str, str]:
    with SAVED_LOCK:
        access = str(SAVED.get("access_token") or "")
        expires_at = float(SAVED.get("expires_at") or 0)
        mcp_url = str(SAVED.get("mcp_url") or DEEPWIKI_URL)
    if access and time.time() < expires_at - TOKEN_SKEW_SECONDS:
        return access, mcp_url
    return "", mcp_url


def reuse_log() -> dict:
    with SAVED_LOCK:
        remaining = max(0, int(float(SAVED.get("expires_at") or 0) - time.time()))
    return {
        "step": "saved gateway token",
        "elapsed_ms": 0,
        "request": {"method": "GET", "url": DEEPWIKI_URL, "headers": {}, "body": {}},
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "credential": "Saved AI Gateway access token",
                "expires_in": remaining,
                "detail": "The agent reused the gateway token. The approval screen stays closed until this token expires.",
            },
        },
    }


def refresh_saved_token() -> tuple[list[dict], str, str]:
    with SAVED_LOCK:
        refresh = str(SAVED.get("refresh_token") or "")
        endpoint = str(SAVED.get("token_endpoint") or "")
        resource = str(SAVED.get("resource") or "")
        mcp_url = str(SAVED.get("mcp_url") or DEEPWIKI_URL)
    if not refresh or not endpoint:
        return [], "", mcp_url
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": CLIENT_ID,
    }
    if resource:
        form["resource"] = resource
    safe_form = dict(form)
    safe_form["refresh_token"] = redact(refresh)
    status, response_headers, response_body, elapsed, sent_headers = http_request(
        endpoint,
        method="POST",
        data=form,
    )
    parsed = parse_body(response_body)
    shown = dict(parsed) if isinstance(parsed, dict) else parsed
    if isinstance(shown, dict):
        for key in ("access_token", "refresh_token", "id_token"):
            if key in shown:
                shown[key] = redact(str(shown[key]))
    entry = log_entry(
        "token refresh",
        "POST",
        endpoint,
        sent_headers,
        safe_form,
        status,
        response_headers,
        b"",
        elapsed,
    )
    entry["response"]["body"] = shown
    if isinstance(parsed, dict) and status < 400 and parsed.get("access_token"):
        remember_token(parsed, resource, endpoint, mcp_url)
        return [entry], str(parsed["access_token"]), mcp_url
    clear_saved_token()
    return [entry], "", mcp_url


def callback_log(state: str, code: str, error: str) -> dict:
    return {
        "step": "authorization callback",
        "elapsed_ms": 0,
        "request": {
            "method": "GET",
            "url": REDIRECT_URI,
            "headers": {},
            "body": {
                "state": redact(state) if state else "",
                "code": redact(code) if code else "",
                "error": error,
            },
        },
        "response": {"status": 200 if code else 400, "headers": {}, "body": {"received": bool(code)}},
    }


def authorization_redirect_log(url: str) -> dict:
    return {
        "step": "authorization redirect",
        "elapsed_ms": 0,
        "request": {"method": "GET", "url": url, "headers": {}, "body": {}},
        "response": {
            "status": 302,
            "headers": {},
            "body": {
                "detail": "The browser follows the authorization endpoint discovered for deepwikimcp. After Idira signs the user in, the gateway returns to this callback.",
            },
        },
    }


def store_result(logs: list[dict]) -> str:
    result_id = secrets.token_urlsafe(16)
    with RESULTS_LOCK:
        now = time.time()
        stale = [key for key, value in RESULTS.items() if now - float(value["created"]) > 1800]
        for key in stale:
            RESULTS.pop(key, None)
        RESULTS[result_id] = {"created": now, "logs": logs}
    return result_id


def flow_begin() -> None:
    with FLOW_LOCK:
        FLOW["phase"] = "calling"
        FLOW["steps"] = []


def flow_note(phase: str, step: str, status: int, detail: str = "") -> None:
    with FLOW_LOCK:
        FLOW["phase"] = phase
        steps = FLOW.setdefault("steps", [])
        if isinstance(steps, list):
            steps.append({"step": step, "status": status, "detail": detail})
            del steps[:-30]


def flow_from_logs(phase: str, logs: list[dict]) -> None:
    for entry in logs:
        response = entry.get("response") or {}
        status = int(response.get("status") or 0)
        flow_note(phase, str(entry.get("step") or "step"), status)


def flow_snapshot() -> dict:
    with FLOW_LOCK:
        steps = FLOW.get("steps")
        return {"phase": str(FLOW.get("phase") or "idle"), "steps": list(steps) if isinstance(steps, list) else []}


def from_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1"} or host.startswith("::ffff:127.")


class App(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.respond(200, f"Python agent. Open {FRONTEND}/\n".encode(), "text/plain; charset=utf-8")
            return
        if parsed.path == "/run":
            self.start_run()
            return
        if parsed.path == "/callback":
            self.finish_login(urllib.parse.parse_qs(parsed.query))
            return
        if parsed.path.startswith("/results/"):
            self.show_result(parsed.path.removeprefix("/results/"))
            return
        if parsed.path == "/trace":
            self.show_trace()
            return
        self.respond(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/call":
            self.respond(404, b"Not found", "text/plain; charset=utf-8")
            return
        if not from_loopback(self.client_address[0]):
            self.respond(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            payload = {}
        token = str(payload.get("access_token", "")) if isinstance(payload, dict) else ""
        if not token:
            self.respond(400, b'{"error":"missing access token"}\n', "application/json")
            return
        logs = [
            {
                "step": "user credential",
                "elapsed_ms": 0,
                "request": {"method": "POST", "url": f"{FRONTEND}/call", "headers": {}, "body": {}},
                "response": {
                    "status": 200,
                    "headers": {},
                    "body": {
                        "credential": "Microsoft access token for the signed-in user",
                        "detail": "The agent calls deepwikimcp with this token. The token value is not recorded.",
                    },
                },
            }
        ]
        logs.extend(listed_tool_logs(token, DEEPWIKI_URL))
        self.respond(200, json.dumps({"logs": logs}).encode(), "application/json")

    def start_run(self) -> None:
        logs: list[dict] = []
        flow_begin()
        access, mcp_url = current_access_token()
        if access:
            logs.append(reuse_log())
            flow_note("saved", "saved gateway token", 200, "reused access token")
            call_logs = listed_tool_logs(access, mcp_url)
            flow_from_logs("mcp", call_logs)
            if not call_failed(call_logs[0]):
                flow_note("done", "result", 200, "returned to the web app")
                self.finish_with(logs + call_logs)
                return
            logs.extend(call_logs)
            with SAVED_LOCK:
                SAVED["access_token"] = ""
                SAVED["expires_at"] = 0
        refresh_logs, access, mcp_url = refresh_saved_token()
        logs.extend(refresh_logs)
        if refresh_logs:
            flow_from_logs("refresh", refresh_logs)
        if access:
            call_logs = listed_tool_logs(access, mcp_url)
            flow_from_logs("mcp", call_logs)
            logs.extend(call_logs)
            if not call_failed(call_logs[0]):
                flow_note("done", "result", 200, "returned to the web app")
                self.finish_with(logs)
                return
            clear_saved_token()
        discovery, oauth = discover_oauth(DEEPWIKI_URL)
        logs.extend(discovery)
        flow_from_logs("calling", discovery)
        if oauth is None:
            flow_note("error", "discover", 401, "gateway metadata was not available")
            self.finish_with(logs)
            return
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(16)
        target = authorize_url(
            oauth["authorization_endpoint"],
            state,
            challenge,
            oauth["resource"],
            oauth["scope"],
        )
        with PENDING_LOCK:
            now = time.time()
            stale = [key for key, value in PENDING.items() if now - float(value["created"]) > 600]
            for key in stale:
                PENDING.pop(key, None)
            PENDING[state] = {
                "verifier": verifier,
                "created": str(now),
                "token_endpoint": oauth["token_endpoint"],
                "resource": oauth["resource"],
                "mcp_url": oauth["mcp_url"],
                "authorize_url": target,
                "discovery": logs,
            }
        flow_note("approve", "AIS", 302, "waiting for AIS approval")
        self.redirect(target)

    def finish_login(self, params: dict[str, list[str]]) -> None:
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        error = params.get("error_description", params.get("error", [""]))[0]
        with PENDING_LOCK:
            pending = PENDING.pop(state, None)
        logs: list[dict] = list(pending.get("discovery") or []) if pending else []
        if pending and pending.get("authorize_url"):
            logs.append(authorization_redirect_log(str(pending["authorize_url"])))
        logs.append(callback_log(state, code, error))
        if error or not pending or not code:
            logs[-1]["response"] = {
                "status": 400,
                "headers": {},
                "body": {"error": error or "This sign-in session is missing or already used."},
            }
            logs.insert(-1, idira_return_log(params, None, logs[-1]["response"]["body"]))
            self.trace("rejected", params, logs[-2]["response"]["body"])
            flow_note("error", "AIS", 400, error or "approval did not return a code")
            self.finish_with(logs)
            return
        token, token_log = exchange_code(code, pending["verifier"], pending["resource"], pending["token_endpoint"])
        logs.insert(-1, idira_return_log(params, token, token_log["response"]["body"]))
        logs.append(token_log)
        flow_note("approve", "AIS", 200, "approval returned")
        if token is None:
            self.trace("token exchange failed", params, token_log["response"]["body"])
            flow_note("error", "token exchange", int(token_log["response"]["status"]), "gateway did not issue a token")
            self.finish_with(logs)
            return
        self.trace("access token received", params, {"token_type": token.get("token_type"), "scope": token.get("scope"), "expires_in": token.get("expires_in")})
        flow_note("token", "gateway token", 200, "access token and refresh token")
        remember_token(token, str(pending["resource"]), str(pending["token_endpoint"]), str(pending["mcp_url"]))
        call_logs = listed_tool_logs(token["access_token"], pending["mcp_url"])
        logs.extend(call_logs)
        flow_from_logs("mcp", call_logs)
        if call_failed(call_logs[0]):
            flow_note("error", "MCP server", int(call_logs[0]["response"]["status"]), "deepwiki rejected the gateway token")
        else:
            flow_note("done", "result", 200, "returned to the web app")
        self.finish_with(logs)

    def show_trace(self) -> None:
        if not from_loopback(self.client_address[0]):
            self.respond(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        self.respond(200, json.dumps(flow_snapshot()).encode(), "application/json")

    def show_result(self, result_id: str) -> None:
        if not from_loopback(self.client_address[0]):
            self.respond(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        with RESULTS_LOCK:
            found = RESULTS.get(result_id)
            logs = list(found["logs"]) if found else None
        if logs is None:
            self.respond(404, b'{"error":"unknown result"}\n', "application/json")
            return
        self.respond(200, json.dumps({"logs": logs}).encode(), "application/json")

    def finish_with(self, logs: list[dict]) -> None:
        result_id = store_result(logs)
        self.redirect(f"{FRONTEND}/called?{urllib.parse.urlencode({'rid': result_id})}")

    def trace(self, outcome: str, params: dict[str, list[str]], detail: object) -> None:
        payload = {"outcome": outcome, "query": public_params(params), "detail": detail}
        sys.stderr.write("OAuth2 GET /callback\n%s\n" % json.dumps(payload, indent=2, default=str))

    def redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def respond(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve() -> None:
    parsed = urllib.parse.urlparse(REDIRECT_URI)
    frontend = urllib.parse.urlparse(FRONTEND)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/callback" or (parsed.port or 80) != 9999:
        raise SystemExit(f"AIS_REDIRECT_URI must be http://localhost:9999/callback, got {REDIRECT_URI}")
    if frontend.hostname not in {"127.0.0.1", "localhost"} or (frontend.port or 80) != 7777:
        raise SystemExit(f"AIS_FRONTEND_URL must be http://localhost:7777, got {FRONTEND}")
    port = parsed.port or 9999
    servers: list[ThreadingHTTPServer] = []
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            class BoundServer(ThreadingHTTPServer):
                address_family = family
                daemon_threads = True

            server = BoundServer((address, port), App)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    if not servers:
        raise SystemExit(f"Could not listen on port {port}.")
    print(f"Agent http://localhost:{port}/")
    print(f"Callback stays {REDIRECT_URI}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        sys.exit(130)
    except KeyError as missing:
        sys.exit(f"Missing environment variable {missing}")
