#!/usr/bin/env python3
"""Entra sign-in page for the Python agent.

Open http://localhost:7777. After Entra sign-in, the page can send the browser
to the agent on port 9999 and then show the logs from that call.
"""

from __future__ import annotations

import base64
import hashlib
import html
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
import webbrowser
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

PORT = int(os.environ.get("AIS_FRONTEND_PORT", "7777"))
REDIRECT_URI = os.environ.get("WEBAPP_REDIRECT_URI", f"http://localhost:{PORT}/callback")
ENTRA_TENANT = os.environ.get("WEBAPP_ENTRA_TENANT", "oogwaysanhotmail.onmicrosoft.com")
ENTRA_CLIENT_ID = os.environ.get("WEBAPP_ENTRA_CLIENT_ID", "")
GATEWAY_CLIENT_ID = os.environ.get("AGENT_CLIENT_ID", "")
GATEWAY_REDIRECT_URI = os.environ.get("AGENT_REDIRECT_URI", "http://localhost:9999/callback")
ENTRA_SCOPE = "openid profile email"
ENTRA_AUTHORIZE = f"https://login.microsoftonline.com/{ENTRA_TENANT}/oauth2/v2.0/authorize"
ENTRA_TOKEN = f"https://login.microsoftonline.com/{ENTRA_TENANT}/oauth2/v2.0/token"
AGENT_URL = os.environ.get("AIS_AGENT_URL", "http://localhost:9999").rstrip("/")
AGENT_RESULTS = os.environ.get("AIS_AGENT_RESULTS", "http://127.0.0.1:9999").rstrip("/")
COOKIE = "ais_session"

PENDING: dict[str, dict[str, str]] = {}
PENDING_LOCK = threading.Lock()
SESSIONS: dict[str, dict[str, str]] = {}
SESSIONS_LOCK = threading.Lock()
ENTRA_FLOW = {"phase": "idle", "user": ""}
ENTRA_FLOW_LOCK = threading.Lock()


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


def parse_body(raw: bytes) -> object:
    text = raw.decode(errors="replace")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"raw": text[:4000]}


def http_form(url: str, form: dict[str, str]) -> tuple[int, object]:
    encoded = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "ais-frontend/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, parse_body(response.read())
    except urllib.error.HTTPError as error:
        return error.code, parse_body(error.read())


def authorize_url(state: str, challenge: str, nonce: str) -> str:
    params = [
        ("client_id", ENTRA_CLIENT_ID),
        ("response_type", "code"),
        ("redirect_uri", REDIRECT_URI),
        ("response_mode", "query"),
        ("scope", ENTRA_SCOPE),
        ("state", state),
        ("nonce", nonce),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        ("prompt", "select_account"),
    ]
    return ENTRA_AUTHORIZE + "?" + urllib.parse.urlencode(params)


def identity_from_token(token: dict) -> dict[str, str]:
    payload = jwt_payload(str(token.get("id_token", "")))
    kept = {}
    for key in ("preferred_username", "name", "oid", "tid"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            kept[key] = value
    return kept


def exchange_code(code: str, verifier: str) -> tuple[dict | None, object]:
    status, parsed = http_form(
        ENTRA_TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": ENTRA_CLIENT_ID,
        },
    )
    if isinstance(parsed, dict) and status < 400 and "access_token" in parsed:
        return parsed, public_token(parsed)
    return None, parsed


def public_token(token: dict) -> dict:
    shown = {}
    for key, value in token.items():
        if key in {"access_token", "refresh_token", "id_token"}:
            shown[key] = redact(str(value))
        else:
            shown[key] = value
    return shown


def call_agent(access_token: str) -> tuple[list[dict] | None, str]:
    request = urllib.request.Request(
        f"{AGENT_RESULTS}/call",
        data=json.dumps({"access_token": access_token}).encode(),
        headers={"User-Agent": "ais-frontend/1.0", "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            parsed = parse_body(response.read())
    except urllib.error.HTTPError as error:
        return None, f"The agent returned HTTP {error.code}."
    except urllib.error.URLError:
        return None, "The Python agent on port 9999 is not running."
    logs = parsed.get("logs") if isinstance(parsed, dict) else None
    if not isinstance(logs, list):
        return None, "The agent did not return logs."
    return logs, ""


def fetch_logs(result_id: str) -> tuple[list[dict] | None, str]:
    url = f"{AGENT_RESULTS}/results/{urllib.parse.quote(result_id)}"
    request = urllib.request.Request(url, headers={"User-Agent": "ais-frontend/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            parsed = parse_body(response.read())
    except urllib.error.HTTPError as error:
        return None, f"The agent returned HTTP {error.code}."
    except urllib.error.URLError:
        return None, "The Python agent on port 9999 is not running."
    logs = parsed.get("logs") if isinstance(parsed, dict) else None
    if not isinstance(logs, list):
        return None, "The agent result did not include logs."
    return logs, ""


def page(title: str, body: str, status: int = 200) -> tuple[int, bytes]:
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c1915;
      --muted: #6b6258;
      --line: #e4ddd3;
      --paper: #f6f1ea;
      --card: #fffdf9;
      --accent: #9a3412;
      --ok: #166534;
      --bad: #9f1239;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font: 16px/1.5 "Iowan Old Style", Palatino, Georgia, serif;
    }}
    main {{ width: min(980px, calc(100% - 32px)); margin: 32px auto 64px; }}
    header p {{
      margin: 0 0 6px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font: 12px/1.2 ui-sans-serif, system-ui, sans-serif;
      color: var(--accent);
    }}
    h1 {{ margin: 0 0 8px; font-size: 32px; font-weight: 600; letter-spacing: -0.03em; }}
    .lede, .meta {{ color: var(--muted); }}
    .lede {{ margin: 0 0 24px; max-width: 70ch; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
    }}
    a.button, button.button {{
      display: inline-block;
      margin-top: 14px;
      margin-right: 8px;
      background: var(--ink);
      color: #fffdf9;
      text-decoration: none;
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      cursor: pointer;
      font: 600 15px/1 ui-sans-serif, system-ui, sans-serif;
    }}
    a.button:hover, button.button:hover {{ background: var(--accent); }}
    a.quiet {{ color: var(--muted); font: 14px/1 ui-sans-serif, system-ui, sans-serif; }}
    .tabs {{ display: flex; gap: 8px; margin: 0 0 22px; }}
    .tabs a {{
      color: var(--muted);
      text-decoration: none;
      border-radius: 999px;
      padding: 8px 12px;
      font: 600 14px/1 ui-sans-serif, system-ui, sans-serif;
    }}
    .tabs a.active {{ background: var(--ink); color: #fffdf9; }}
    .lanes {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }}
    .lane h2 {{ margin: 0 0 12px; font-size: 20px; }}
    .stage {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
    }}
    .arrow {{ margin: 6px 0; color: var(--accent); text-align: center; font: 600 18px/1 ui-sans-serif, system-ui, sans-serif; }}
    .num {{ margin-right: 6px; color: var(--accent); font: 600 12px/1 ui-sans-serif, system-ui, sans-serif; }}
    .stage h3 {{ display: inline; margin: 0; font-size: 17px; }}
    .stage p {{ margin: 8px 0 0; color: var(--muted); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
    .chip {{ border-radius: 999px; padding: 4px 8px; font: 12px/1.2 ui-sans-serif, system-ui, sans-serif; }}
    .token {{ background: #fff1e6; color: #9a3412; }}
    .data {{ background: #ecfdf3; color: #166534; }}
    .legend {{ margin: 0 0 18px; }}
    .run {{ margin-top: 16px; }}
    .run li {{ margin: 8px 0; }}
    .diagram {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 8px 8px 0;
      overflow: auto;
    }}
    .diagram svg {{ max-width: 100%; height: auto; }}
    .live {{ margin: 0 0 14px; font: 15px/1.4 ui-sans-serif, system-ui, sans-serif; }}
    @media (max-width: 800px) {{ .lanes {{ grid-template-columns: 1fr; }} }}
    .log {{ margin-top: 14px; border-top: 1px solid var(--line); padding-top: 12px; }}
    .log h3 {{ margin: 0; font-size: 16px; }}
    .status {{ font: 13px/1 ui-sans-serif, system-ui, sans-serif; }}
    .ok {{ color: var(--ok); }}
    .bad {{ color: var(--bad); }}
    pre {{
      margin: 8px 0 0;
      padding: 12px;
      overflow: auto;
      max-height: 280px;
      background: #211c17;
      color: #f6efe6;
      border-radius: 12px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
  </style>
</head>
<body>
  <main>
    {body}
  </main>
</body>
</html>
"""
    return status, document.encode()


def header_lines(headers: dict) -> str:
    if not headers:
        return "(none)"
    return "\n".join(f"{key}: {value}" for key, value in headers.items())


def call_failed(entry: dict) -> bool:
    response = entry.get("response") or {}
    status = response.get("status", 0)
    if status >= 400 or status == 0:
        return True
    body = response.get("body")
    if not isinstance(body, dict):
        return False
    if "error" in body and "sse" not in body:
        return True
    sse = body.get("sse")
    return isinstance(sse, dict) and "error" in sse


def render_logs(logs: list[dict]) -> str:
    if not logs:
        return '<p class="meta">No requests yet.</p>'
    blocks = []
    for entry in logs:
        status = entry.get("response", {}).get("status", 0)
        mark = "bad" if call_failed(entry) or status == 0 else "ok"
        request = entry.get("request") or {}
        wire = {
            "method": request.get("method", ""),
            "url": request.get("url", ""),
            "body": request.get("body", {}),
        }
        blocks.append(
            "<article class=\"log\">"
            f"<h3>{html.escape(str(entry.get('step', 'request')))} "
            f"<span class=\"status {mark}\">HTTP {status} · {entry.get('elapsed_ms', 0)} ms</span></h3>"
            "<p class=\"meta\">Headers sent</p>"
            f"<pre>{html.escape(header_lines(request.get('headers') or {}))}</pre>"
            "<p class=\"meta\">Request</p>"
            f"<pre>{html.escape(json.dumps(wire, indent=2)[:12000])}</pre>"
            "<p class=\"meta\">Response</p>"
            f"<pre>{html.escape(json.dumps(entry.get('response', {}), indent=2)[:12000])}</pre>"
            "</article>"
        )
    return "".join(blocks)


def display_name(identity: dict[str, str]) -> str:
    return identity.get("preferred_username") or identity.get("name") or "Signed in"


def tabs(active: str) -> str:
    call = "active" if active == "call" else ""
    flow = "active" if active == "flow" else ""
    return (
        '<nav class="tabs">'
        f'<a class="{call}" href="/">Call</a>'
        f'<a class="{flow}" href="/flow">Flow</a>'
        "</nav>"
    )


def registration(client_id: str, redirect_uri: str) -> str:
    return (
        f'<p class="meta">Client <code>{html.escape(client_id)}</code><br>'
        f"Redirect <code>{html.escape(redirect_uri)}</code></p>"
    )


def set_entra(phase: str, user: str = "") -> None:
    with ENTRA_FLOW_LOCK:
        ENTRA_FLOW["phase"] = phase
        ENTRA_FLOW["user"] = user


def entra_state() -> dict[str, str]:
    with ENTRA_FLOW_LOCK:
        return {"phase": str(ENTRA_FLOW["phase"]), "user": str(ENTRA_FLOW["user"])}


def agent_trace() -> dict:
    request = urllib.request.Request(
        f"{AGENT_RESULTS}/trace",
        headers={"User-Agent": "ais-frontend/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            parsed = parse_body(response.read())
    except (urllib.error.URLError, TimeoutError):
        return {"phase": "idle", "steps": [], "detail": "The Python agent is not running."}
    if not isinstance(parsed, dict):
        return {"phase": "idle", "steps": []}
    steps = parsed.get("steps")
    return {"phase": str(parsed.get("phase") or "idle"), "steps": steps if isinstance(steps, list) else []}


def live_state(identity: dict[str, str] | None) -> dict:
    entra = entra_state()
    user = (identity or {}).get("preferred_username") or entra["user"]
    phase = "signed-in" if identity else entra["phase"]
    return {"user": user, "entra": phase, "agent": agent_trace()}


FLOW_SCRIPT = r"""
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    flowchart: { curve: "basis", htmlLabels: true, padding: 16 },
    themeVariables: {
      fontFamily: "ui-sans-serif, system-ui, sans-serif",
      primaryColor: "#fffdf9",
      primaryBorderColor: "#e4ddd3",
      primaryTextColor: "#1c1915",
      lineColor: "#b7aea3",
      background: "#fffdf9"
    }
  });
  const names = {
    User: "User",
    WebApp: "Web app",
    Entra: "Entra Directory",
    Agent: "Python agent",
    AIGW: "AIGW",
    AIS: "AIS / Idira",
    MCP: "MCP server"
  };
  const phrases = {
    idle: "Waiting for a sign-in or an agent call.",
    authorize: "The user is signing in through Entra Directory.",
    "signed-in": "Entra has returned the user token to the web app.",
    calling: "The web app is calling the Python agent.",
    saved: "The agent is reusing the saved gateway token.",
    refresh: "The agent is refreshing the gateway token.",
    approve: "AIGW is authenticating the user through AIS/Idira and Entra Directory.",
    token: "AIGW issued the gateway access token.",
    mcp: "The agent is calling the MCP server through AIGW.",
    done: "The MCP result is back on the web app.",
    error: "The live call stopped."
  };

  function story(state) {
    const phase = (state.agent && state.agent.phase) || "idle";
    const steps = (state.agent && state.agent.steps) || [];
    const saved = steps.some((step) => step.step === "saved gateway token") || phase === "saved" || phase === "refresh";
    const approved = steps.some((step) => step.step === "AIS" && step.status === 200);
    const mcpBad = steps.some((step) => String(step.step).startsWith("deepwikimcp") && step.status >= 400);
    const mcp = steps.some((step) => String(step.step).startsWith("deepwikimcp"));
    const beats = [];
    const push = (from, to, label, caption, skip = false) => beats.push({ from, to, label, caption, skip });
    if (state.entra !== "idle" || state.user || phase !== "idle") {
      push("User", "WebApp", "opens", "The user opens the web app.");
    }
    if (state.entra === "authorize" || state.entra === "signed-in" || state.user || state.entra === "error") {
      push("WebApp", "Entra", "sign-in", "The web app sends the user to Entra Directory.");
    }
    if (state.entra === "signed-in" || state.user) {
      push("Entra", "WebApp", "user token", "Entra returns the user token to the web app.");
    }
    if (phase !== "idle") {
      push("WebApp", "Agent", "call", "The web app calls the Python agent.");
      push("Agent", "AIGW", "gateway token", saved ? "The agent uses the saved gateway token." : "The agent reaches AIGW.");
    }
    if (phase !== "idle") {
      if (saved) {
        push("AIGW", "AIS", "authenticate", "Saved token: AIGW does not authenticate again through AIS/Idira.", true);
        push("AIS", "Entra", "authenticate", "Saved token: AIS/Idira does not call Entra Directory.", true);
        push("Entra", "AIS", "authenticated", "Saved token: Entra does not authenticate the user again.", true);
        push("AIS", "AIGW", "authenticated", "Saved token: no new authentication returns to AIGW.", true);
        push("AIGW", "AIS", "authorize", "Saved token: AIGW does not ask AIS/Idira to authorize again.", true);
        push("AIS", "AIGW", "authorized", "Saved token: the earlier AIS/Idira authorization is reused.", true);
      } else if (phase === "approve" || approved) {
        push("AIGW", "AIS", "authenticate", "AIGW sends the user to AIS/Idira to authenticate.");
        push("AIS", "Entra", "authenticate", "AIS/Idira sends the user to Entra Directory.");
        if (approved) {
          push("Entra", "AIS", "authenticated", "Entra Directory authenticates the user for AIS/Idira.");
          push("AIS", "AIGW", "authenticated", "AIS/Idira confirms the authenticated user to AIGW.");
          push("AIGW", "AIS", "authorize", "AIGW asks AIS/Idira to authorize the MCP call.");
          push("AIS", "AIGW", "authorized", "AIS/Idira authorizes the agent, and AIGW issues the token.");
        }
      }
    }
    if (saved || phase === "token" || phase === "mcp" || phase === "done" || mcp || mcpBad) {
      push("AIGW", "MCP", "MCP data", mcpBad ? "The MCP server rejected the call." : "AIGW calls the MCP server.");
    }
    if (phase === "done") {
      push("Agent", "WebApp", "result", "The result returns to the web app.");
    }
    return { beats, steps };
  }

  function diagram(beats) {
    const lines = ["flowchart LR"];
    for (const [id, label] of Object.entries(names)) lines.push(`  ${id}["${label}"]`);
    beats.forEach((beat) => lines.push(`  ${beat.from} -->|${beat.label}| ${beat.to}`));
    lines.push("  classDef idle fill:#fffdf9,stroke:#e4ddd3,color:#1c1915");
    lines.push("  class User,WebApp,Entra,Agent,AIGW,AIS,MCP idle");
    beats.forEach((beat, index) => lines.push(`  linkStyle ${index} stroke:${beat.skip ? "#b7aea3" : "#9a3412"},stroke-width:2.5px`));
    return lines.join("\n");
  }

  function edgePaths() {
    return [...box.querySelectorAll(".edgePaths path")].filter((path, index, all) => {
      const marker = path.getAttribute("marker-end");
      return Boolean(marker) || all.length === 1;
    });
  }

  function hideEdge(path) {
    const length = path.getTotalLength();
    path.style.animation = "none";
    path.style.strokeDasharray = String(length);
    path.style.strokeDashoffset = String(length);
  }

  function mark(label, color) {
    box.querySelectorAll("g.node").forEach((node) => {
      if (node.textContent.replace(/\s+/g, " ").trim() !== label) return;
      const shape = node.querySelector("rect, polygon, circle, path");
      if (!shape) return;
      shape.style.fill = color === "on" ? "#fff1e6" : color === "skip" ? "#f6f1ea" : "#ecfdf3";
      shape.style.stroke = color === "on" ? "#9a3412" : color === "skip" ? "#b7aea3" : "#166534";
      shape.style.strokeWidth = "2px";
    });
  }

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const box = document.querySelector("#diagram");
  const caption = document.querySelector("#live");
  const list = document.querySelector("#steps");
  let playedKey = "";
  let playedCount = 0;
  let running = false;

  async function play(beats) {
    const key = beats.map((beat) => beat.from + ">" + beat.to + ":" + beat.label).join("|");
    if (!beats.length) {
      playedKey = "";
      playedCount = 0;
      caption.textContent = phrases.idle;
      box.replaceChildren();
      return;
    }
    const restart = !playedKey || !key.startsWith(playedKey);
    if (!restart && playedCount >= beats.length) return;
    if (restart) playedCount = 0;
    playedKey = key;
    const drawn = await mermaid.render("liveflow" + Date.now(), diagram(beats));
    box.innerHTML = drawn.svg;
    const paths = edgePaths();
    paths.forEach(hideEdge);
    for (let index = 0; index < playedCount && index < paths.length; index += 1) {
      paths[index].style.strokeDashoffset = "0";
      mark(names[beats[index].from], beats[index].skip ? "skip" : "ok");
      mark(names[beats[index].to], beats[index].skip ? "skip" : "ok");
    }
    for (let index = playedCount; index < Math.min(beats.length, paths.length); index += 1) {
      const beat = beats[index];
      caption.textContent = beat.caption;
      mark(names[beat.from], beat.skip ? "skip" : "on");
      mark(names[beat.to], beat.skip ? "skip" : "on");
      const path = paths[index];
      if (!path) break;
      path.style.transition = "stroke-dashoffset 0.7s ease";
      path.getBoundingClientRect();
      path.style.strokeDashoffset = "0";
      playedCount = index + 1;
      await wait(850);
      mark(names[beat.from], beat.skip ? "skip" : "ok");
      mark(names[beat.to], beat.skip ? "skip" : "ok");
    }
    playedCount = beats.length;
  }

  async function tick() {
    if (running) return;
    const response = await fetch("/flow/live", { cache: "no-store" });
    const state = await response.json();
    const told = story(state);
    list.replaceChildren();
    const rows = told.steps.length ? told.steps : [{ status: "", step: "No agent call yet.", detail: "" }];
    for (const step of rows) {
      const item = document.createElement("li");
      item.textContent = step.status === "" ? step.step : `HTTP ${step.status} ${step.step}${step.detail ? " · " + step.detail : ""}`;
      list.append(item);
    }
    running = true;
    try {
      await play(told.beats);
    } finally {
      running = false;
    }
  }
  tick();
  setInterval(() => { tick().catch(() => { caption.textContent = "The live flow could not refresh."; }); }, 1000);
</script>
"""


def flow_page() -> tuple[int, bytes]:
    body = f"""
    {tabs("flow")}
    <header>
      <p>Live flow</p>
      <h1>User to MCP server</h1>
      <p class="lede">The main path is the user, the web app, the Python agent, AIGW, and the MCP server. For a new gateway token, AIGW authenticates the user through AIS/Idira and Entra Directory, then AIS/Idira authorizes the MCP call.</p>
    </header>
    <p class="live" id="live">Waiting for a sign-in or an agent call.</p>
    <section class="diagram" id="diagram" aria-label="Live flow diagram"></section>
    <section class="card run">
      <h2>What just moved</h2>
      <ol id="steps"><li>No agent call yet.</li></ol>
    </section>
    {FLOW_SCRIPT}
    """
    return page("Live flow", body)



def home(identity: dict[str, str] | None, notice: str = "", logs: list[dict] | None = None) -> tuple[int, bytes]:
    notice_html = f'<p class="meta">{html.escape(notice)}</p>' if notice else ""
    logs_html = render_logs(logs) if logs is not None else ""
    if identity:
        fields = "".join(
            f"<p class=\"meta\">{html.escape(label)} <code>{html.escape(identity[key])}</code></p>"
            for label, key in (("User", "preferred_username"), ("Name", "name"), ("Object", "oid"), ("Tenant", "tid"))
            if identity.get(key)
        )
        body = f"""
    {tabs("call")}
    <header>
      <p>AI Gateway</p>
      <h1>Call the Python agent</h1>
      <p class="lede">You are signed in. The agent reuses the saved AI Gateway token. The approval screen appears when that token is missing or can no longer be refreshed.</p>
    </header>
    <section class="card">
      {fields}
      {registration(GATEWAY_CLIENT_ID, GATEWAY_REDIRECT_URI)}
      {notice_html}
      <a class="button" href="{html.escape(AGENT_URL)}/run">Call Python Agent</a>
      <a class="quiet" href="/logout">Sign out</a>
      {logs_html}
    </section>
    """
        return page("Call the Python agent", body)
    body = f"""
    {tabs("call")}
    <header>
      <p>Entra ID</p>
      <h1>Sign in</h1>
      <p class="lede">Sign in with your Entra account. This page uses the web app registration below.</p>
    </header>
    <section class="card">
      {registration(ENTRA_CLIENT_ID, REDIRECT_URI)}
      {notice_html}
      <a class="button" href="/login">Sign in</a>
      {logs_html}
    </section>
    """
    return page("Sign in", body)


class App(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.respond(*home(self.session()))
            return
        if parsed.path == "/flow":
            self.respond(*flow_page())
            return
        if parsed.path == "/flow/live":
            self.respond(200, json.dumps(live_state(self.session())).encode(), "application/json")
            return
        if parsed.path == "/login":
            self.start_login()
            return
        if parsed.path == "/callback":
            self.finish_login(urllib.parse.parse_qs(parsed.query))
            return
        if parsed.path == "/called":
            self.show_result(urllib.parse.parse_qs(parsed.query))
            return
        if parsed.path == "/logout":
            self.clear_session()
            self.redirect("/")
            return
        self.respond(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/call":
            self.respond(404, b"Not found", "text/plain; charset=utf-8")
            return
        identity = self.session()
        token = (identity or {}).get("access_token", "")
        if not token:
            self.redirect("/login")
            return
        logs, error = call_agent(token)
        if logs is None:
            self.respond(*home(identity, error))
            return
        self.respond(*home(identity, "", logs))

    def session(self) -> dict[str, str] | None:
        sid = self.cookie()
        if not sid:
            return None
        with SESSIONS_LOCK:
            found = SESSIONS.get(sid)
            return dict(found) if found else None

    def cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value:
                return urllib.parse.unquote(value)
        return ""

    def start_login(self) -> None:
        if not ENTRA_CLIENT_ID:
            self.respond(*home(None, "WEBAPP_ENTRA_CLIENT_ID is not set."))
            return
        set_entra("authorize")
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)
        with PENDING_LOCK:
            now = time.time()
            stale = [key for key, value in PENDING.items() if now - float(value["created"]) > 600]
            for key in stale:
                PENDING.pop(key, None)
            PENDING[state] = {"verifier": verifier, "nonce": nonce, "created": str(now)}
        self.redirect(authorize_url(state, challenge, nonce))

    def finish_login(self, params: dict[str, list[str]]) -> None:
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        error = params.get("error_description", params.get("error", [""]))[0]
        with PENDING_LOCK:
            pending = PENDING.pop(state, None)
        if error or not pending or not code:
            set_entra("error")
            self.respond(*home(self.session(), error or "Entra did not return an authorization code."))
            return
        token, shown = exchange_code(code, pending["verifier"])
        if token is None:
            detail = "Entra token exchange failed."
            if isinstance(shown, dict):
                detail = str(shown.get("error_description") or shown.get("error") or detail)
            set_entra("error")
            self.respond(*home(self.session(), detail))
            return
        payload = jwt_payload(str(token.get("id_token", "")))
        if str(payload.get("nonce", "")) != pending["nonce"]:
            set_entra("error")
            self.respond(*home(self.session(), "The Entra sign-in nonce did not match."))
            return
        identity = identity_from_token(token)
        access_token = str(token.get("access_token", ""))
        if not identity or not access_token:
            set_entra("error")
            self.respond(*home(self.session(), "Microsoft did not return a user access token."))
            return
        set_entra("signed-in", identity.get("preferred_username", ""))
        identity["access_token"] = access_token
        sid = secrets.token_urlsafe(24)
        with SESSIONS_LOCK:
            SESSIONS[sid] = identity
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"{COOKIE}={sid}; HttpOnly; Path=/; SameSite=Lax")
        self.end_headers()

    def show_result(self, params: dict[str, list[str]]) -> None:
        result_id = params.get("rid", [""])[0]
        if not result_id:
            self.respond(*home(self.session(), "The agent did not return a result id."))
            return
        logs, error = fetch_logs(result_id)
        if logs is None:
            self.respond(*home(self.session(), error))
            return
        self.respond(*home(self.session(), "", logs))

    def clear_session(self) -> None:
        set_entra("idle")
        sid = self.cookie()
        if sid:
            with SESSIONS_LOCK:
                SESSIONS.pop(sid, None)

    def redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def respond(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
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
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/callback" or (parsed.port or 80) != PORT:
        raise SystemExit(f"WEBAPP_REDIRECT_URI must be http://localhost:{PORT}/callback, got {REDIRECT_URI}")
    if not ENTRA_CLIENT_ID:
        raise SystemExit("Missing environment variable WEBAPP_ENTRA_CLIENT_ID")
    servers: list[ThreadingHTTPServer] = []
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            class BoundServer(ThreadingHTTPServer):
                address_family = family
                daemon_threads = True

            server = BoundServer((address, PORT), App)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    if not servers:
        raise SystemExit(f"Could not listen on port {PORT}.")
    home_url = f"http://localhost:{PORT}/"
    print(f"Open {home_url}")
    print(f"Entra callback stays {REDIRECT_URI}")
    webbrowser.open(home_url)
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
