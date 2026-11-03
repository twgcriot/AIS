import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Code,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Table,
  Text,
  useHostTheme,
  useState,
} from "cursor/canvas";

type Phase = "Discovery" | "Sign-in" | "Token" | "Tool call";
type ScenarioId = "gwIdp" | "gwCieIdp" | "gwCieAisIdp";
type ClientId = "claude" | "codex" | "vscode";

type Step = {
  from: number;
  to: number;
  phase: Phase;
  label: string;
  title: string;
  why: string;
  code: string;
  holds: string;
  diff?: string;
};

type Client = {
  name: string;
  short: string;
  agentId: string;
  accountNote: string;
  configFile: string;
  configS12: string;
  configS3: string;
  trigger: string;
  registration: string;
  staticSupport: string;
  secretStore: string;
  redirect: string;
  redirectExample: string;
  orgControl: string;
  s3Note: string;
  pros: string[];
  cons: string[];
  fixes: string[];
};

type Takeaway = { title: string; body: string };

type Cell = ReturnType<typeof Text> | string;

type Scenario = {
  name: string;
  heading: string;
  intro: string;
  unusedLanes: number[];
  build: (c: Client) => Step[];
  takeaways: (c: Client) => Takeaway[];
  callout: (c: Client) => { title: string; body: string };
};

const GW_URL = "https://<gateway>/mcp/<server-slug>/mcp";

const CLIENTS: Record<ClientId, Client> = {
  claude: {
    name: "Claude Code",
    short: "Claude Code",
    agentId: "airs-agent-claude-code",
    accountNote: "Your Anthropic or Claude login is not sent to the gateway. The MCP sign-in is a separate OAuth flow.",
    configFile: "~/.claude.json (user scope) or .mcp.json (project scope)",
    configS12: `claude mcp add --transport http \\\n  airs-db ${GW_URL}`,
    configS3: `MCP_CLIENT_SECRET=<agent secret> \\\nclaude mcp add --transport http \\\n  --client-id airs-agent-claude-code \\\n  --client-secret --callback-port 8080 \\\n  airs-db ${GW_URL}`,
    trigger: "run /mcp, select the server and choose Authenticate",
    registration: "Dynamic client registration, or a Client ID Metadata Document if the server supports it",
    staticSupport: "Yes: --client-id, --client-secret, --callback-port",
    secretStore: "System keychain (masked prompt or MCP_CLIENT_SECRET)",
    redirect: "http://localhost:<port>/callback, random port unless --callback-port is set",
    redirectExample: "http://localhost:8080/callback?code=xyz789&state=...",
    orgControl: "Managed settings can allow or deny specific MCP servers",
    s3Note: "Register http://localhost:8080/callback as the agent's redirect URI. Claude Code v2.1.229 sent 127.0.0.1 instead, so use v2.1.231 or later.",
    pros: [
      "Client secret kept in the system keychain, not in config",
      "Fixed callback port matches a pre-registered redirect URI",
      "Managed settings can allow or deny MCP servers",
    ],
    cons: [
      "Requests every advertised scope; you cannot narrow scopes in the client",
      "Redirect URI changed in v2.1.229, so pin a known-good version",
      "Setup is a per-user CLI command",
    ],
    fixes: [
      "Limit the scopes the gateway advertises and grants for this agent, so a broad request still gets a narrow token",
      "Pin Claude Code to v2.1.231 or later in managed settings, and register both localhost and 127.0.0.1 callbacks if allowed",
      "Ship a project-scoped .mcp.json or an onboarding script so users only run /mcp to sign in",
    ],
  },
  codex: {
    name: "Codex",
    short: "Codex",
    agentId: "airs-agent-codex",
    accountNote: "Your ChatGPT or OpenAI login is not sent. Codex's chatgpt auth mode only applies to OpenAI's own first-party servers.",
    configFile: "~/.codex/config.toml or a trusted project's .codex/config.toml",
    configS12: `codex mcp add airs-db \\\n  --url ${GW_URL}\n\n# ~/.codex/config.toml\n[mcp_servers.airs-db]\nurl = "${GW_URL}"`,
    configS3: `codex mcp add airs-db \\\n  --url ${GW_URL} \\\n  --oauth-client-id airs-agent-codex \\\n  --oauth-client-secret "$MCP_CLIENT_SECRET"\n\n[mcp_servers.airs-db.oauth]\nclient_id     = "airs-agent-codex"\nclient_secret = "<saved in plain text>"\ncallback_url  = "http://127.0.0.1/callback"`,
    trigger: "run codex mcp login airs-db (add --no-browser on a headless machine and paste back the redirect URL)",
    registration: "Client ID Metadata Document or dynamic client registration, chosen automatically",
    staticSupport: "Yes: --oauth-client-id, --oauth-client-secret",
    secretStore: "Plain text in config.toml (mcp_servers.<name>.oauth.client_secret)",
    redirect: "http://127.0.0.1/callback, port set with callback_port",
    redirectExample: "http://127.0.0.1/callback?code=xyz789&state=...",
    orgControl: "Check current Codex admin docs for managed MCP configuration",
    s3Note: "The client secret is written to config.toml in plain text. Keep that file private and never commit a project-scoped config that contains it.",
    pros: [
      "Supports both Client ID Metadata Documents and dynamic registration",
      "Headless sign-in with --no-browser for remote machines",
      "Env-var based tokens keep config.toml shareable",
    ],
    cons: [
      "Client secret stored in plain text in config.toml",
      "Org-level MCP controls are less clearly documented",
      "Callback URL must exactly match what was registered",
    ],
    fixes: [
      "Use dynamic registration or a Client ID Metadata Document instead of a static secret, or chmod 600 config.toml and keep it out of git; rotate the agent secret regularly",
      "Enforce control at the gateway: allow only approved servers and tools per workspace, and block unregistered clients",
      "Set a fixed callback_port and register that exact URL on the agent; document it in a shared config template",
    ],
  },
  vscode: {
    name: "GitHub Copilot in VS Code",
    short: "VS Code",
    agentId: "airs-agent-vscode-copilot",
    accountNote: "Your GitHub Copilot sign-in is not sent to the gateway. VS Code runs a separate OAuth flow for the MCP server.",
    configFile: ".vscode/mcp.json (workspace) or the user profile mcp.json",
    configS12: `{\n  "servers": {\n    "airs-db": {\n      "type": "http",\n      "url": "${GW_URL}"\n    }\n  }\n}`,
    configS3: `{\n  "servers": {\n    "airs-db": {\n      "type": "http",\n      "url": "${GW_URL}",\n      "oauth": { "clientId": "airs-agent-vscode-copilot" }\n    }\n  }\n}\n\nSecret: "Set Client Secret" CodeLens above oauth.clientId`,
    trigger: "start the server from mcp.json or the MCP servers view; VS Code opens the browser on first connection",
    registration: "Dynamic client registration when offered, otherwise VS Code prompts for a client ID and secret",
    staticSupport: "Yes: oauth.clientId in mcp.json, secret via the Set Client Secret CodeLens",
    secretStore: "VS Code secret storage, backed by the OS keychain",
    redirect: "VS Code loopback or vscode.dev redirect (confirm exact URIs for your version)",
    redirectExample: "http://127.0.0.1:<port>/?code=xyz789&state=...",
    orgControl: "Copilot Business and Enterprise policies can disable MCP or limit servers to an approved registry",
    s3Note: "Register VS Code's redirect URIs on the agent in Agent Identity Security, and confirm your VS Code version uses the static clientId rather than dynamic registration.",
    pros: [
      "Client secret kept in OS-backed secret storage via CodeLens",
      "Copilot Business and Enterprise policies can restrict MCP servers",
      "Prompts for a client ID when dynamic registration is unavailable",
    ],
    cons: [
      "Dynamic registration may be preferred over a static client ID on some versions",
      "Exact redirect URIs are less clearly documented",
      "Workspace mcp.json is easy to commit; keep only the client ID in it",
    ],
    fixes: [
      "Disable dynamic registration for this client at the gateway so VS Code falls back to the static client ID; test on your pinned version",
      "Capture the redirect URIs from a test sign-in and register those exact values on the agent",
      "Keep secrets only in secret storage, and add a pre-commit or CI secret scan for mcp.json",
    ],
  },
};

const PHASES: Phase[] = ["Discovery", "Sign-in", "Token", "Tool call"];
const CLIENT_LANE = 0;
const GW = 1;
const IDP = 2;
const CIE = 3;
const MCP = 4;

function buildS1(c: Client): Step[] {
  return [
    {
      from: CLIENT_LANE, to: GW, phase: "Discovery",
      label: "MCP request, no token",
      title: `${c.name} calls the gateway's MCP endpoint with no credentials`,
      why: `${c.name} reads the slug-based gateway URL from ${c.configFile}. ${c.accountNote}`,
      code: `POST ${GW_URL}\n{"jsonrpc":"2.0","method":"initialize", ...}`,
      holds: "Nothing",
    },
    {
      from: GW, to: CLIENT_LANE, phase: "Discovery",
      label: "401 + metadata pointer",
      title: "The gateway rejects the request and says where to authenticate",
      why: "The 401 marks this as a protected resource and points to metadata naming the authorization server.",
      code: 'HTTP/1.1 401 Unauthorized\nWWW-Authenticate: Bearer\n  resource_metadata="https://<gateway>/.well-known/oauth-protected-resource"',
      holds: "Nothing",
    },
    {
      from: CLIENT_LANE, to: GW, phase: "Discovery",
      label: "Discover auth server",
      title: `${c.short} discovers the gateway's OAuth endpoints`,
      why: "The metadata lists the authorize, token and registration endpoints. Nothing is hard-coded in the client.",
      code: "GET /.well-known/oauth-protected-resource\nGET /.well-known/oauth-authorization-server\n  -> authorization_endpoint, token_endpoint,\n     registration_endpoint",
      holds: "Nothing",
    },
    {
      from: CLIENT_LANE, to: GW, phase: "Discovery",
      label: "Register client",
      title: `${c.short} registers itself as an anonymous OAuth client`,
      why: `${c.registration}. Any MCP client can do this, so the gateway cannot tell ${c.short} apart from other tools.`,
      code: `POST /oauth/register\n{"client_name":"${c.name}",\n "redirect_uris":["${c.redirectExample.split("?")[0]}"]}\n\n-> {"client_id":"abc123"}`,
      holds: "Client ID",
    },
    {
      from: CLIENT_LANE, to: GW, phase: "Sign-in",
      label: "Browser: /authorize + PKCE",
      title: `${c.short} opens your browser at the gateway's authorize page`,
      why: `To start sign-in, ${c.trigger}. The client keeps a random PKCE verifier and sends only its hash, so only it can redeem the code later.`,
      code: "GET /oauth/authorize?response_type=code\n  &client_id=abc123\n  &code_challenge=SHA256(verifier)\n  &code_challenge_method=S256",
      holds: "Client ID, PKCE verifier",
    },
    {
      from: GW, to: IDP, phase: "Sign-in",
      label: "Redirect to IdP SSO",
      title: "The gateway hands the login to your IdP",
      why: "Okta or Entra ID is configured as the gateway's SSO provider over OIDC or SAML, so company login policy and MFA apply.",
      code: "302 Found\nLocation: https://login.company.com/authorize?...\n(OIDC or SAML)",
      holds: "Client ID, PKCE verifier",
    },
    {
      from: IDP, to: GW, phase: "Sign-in",
      label: "Identity + group claims",
      title: "The IdP returns who you are, including your groups",
      why: "With no directory sync, the groups inside this token are the only group data the gateway has. They reflect the moment you signed in.",
      code: '{"sub":"u-1029",\n "email":"dev@company.com",\n "groups":["eng-platform"]}',
      holds: "Client ID, PKCE verifier",
    },
    {
      from: GW, to: CLIENT_LANE, phase: "Sign-in",
      label: "Auth code via redirect",
      title: `The gateway redirects back to ${c.short} with a one-time code`,
      why: `${c.short}'s redirect URI is ${c.redirect}. The code is short-lived, single use, and useless without the PKCE verifier.`,
      code: c.redirectExample,
      holds: "One-time auth code",
    },
    {
      from: CLIENT_LANE, to: GW, phase: "Token",
      label: "Code + PKCE verifier",
      title: `${c.short} redeems the code as a public client`,
      why: "No client secret is involved. PKCE proves the same app started and finished the flow.",
      code: "POST /oauth/token\ngrant_type=authorization_code\n&code=xyz789\n&code_verifier=<original secret>",
      holds: "One-time auth code",
    },
    {
      from: GW, to: CLIENT_LANE, phase: "Token",
      label: "Access + refresh token",
      title: "The gateway issues tokens that identify you only",
      why: `The token carries the user and their groups from sign-in. It says nothing reliable about whether ${c.short} or some other tool is using it.`,
      code: '{"access_token":"eyJ...",\n "token_type":"Bearer",\n "expires_in":3600,\n "refresh_token":"..."}\n\nsub    dev@company.com\ngroups eng-platform',
      holds: "Access token and refresh token",
    },
    {
      from: CLIENT_LANE, to: GW, phase: "Tool call",
      label: "tools/call + Bearer",
      title: "Every MCP call carries your access token",
      why: `Whenever the ${c.short} agent decides to use a tool, the request includes the Bearer token.`,
      code: `POST ${GW_URL}\nAuthorization: Bearer eyJ...\n{"method":"tools/call",\n "params":{"name":"read_query", ...}}`,
      holds: "Access token and refresh token",
    },
    {
      from: GW, to: GW, phase: "Tool call",
      label: "Check token + group policy",
      title: "The gateway authorizes using groups from the token",
      why: "Authentication checks the token. Authorization maps the token's groups to a workspace and to allowed servers and tools. Group changes only take effect at next sign-in.",
      code: 'token  signature, expiry, audience           ok\ngroups eng-platform (from token claims)\nserver allowed for eng-platform?             ok\ntool   "read_query" enabled?                ok',
      holds: "Access token and refresh token",
    },
    {
      from: GW, to: MCP, phase: "Tool call",
      label: "Call with stored credential",
      title: "The gateway calls the MCP server with a credential it holds",
      why: "Your token is never forwarded. Credentials are stored in gateway Integrations or referenced from your KMS.",
      code: "POST <backend URL from MCP Registry>\nAuthorization: Bearer <gateway-held or per-user token>",
      holds: "Access token and refresh token",
    },
    {
      from: MCP, to: GW, phase: "Tool call",
      label: "Tool result",
      title: "The MCP server returns the result to the gateway",
      why: "The MCP server should accept traffic only from the gateway.",
      code: '{"jsonrpc":"2.0","result":{"content":[ ... ]}}',
      holds: "Access token and refresh token",
    },
    {
      from: GW, to: CLIENT_LANE, phase: "Tool call",
      label: "Result + user audit log",
      title: "The gateway returns the result and logs the user",
      why: `Logs show which person made the call, but not that it came through ${c.short}.`,
      code: "audit  user=dev@company.com  server=<server-slug>\n       tool=read_query  outcome=allow",
      holds: "Access token and refresh token",
    },
  ];
}

function buildS2(c: Client): Step[] {
  const s1 = buildS1(c);
  return [
    ...s1.slice(0, 6),
    {
      from: IDP, to: GW, phase: "Sign-in",
      label: "User identity",
      title: "The IdP returns who you are; groups can come later",
      why: "The token only needs a stable user attribute. Groups will be read from the directory at call time instead of trusted from the token.",
      code: '{"upn":"dev@company.com",\n "mfa":true}',
      holds: "Client ID, PKCE verifier",
      diff: "Groups no longer need to ride in the token. The user attribute (for example UserPrincipalName) must match the one CIE syncs.",
    },
    ...s1.slice(7, 11),
    {
      from: GW, to: CIE, phase: "Tool call",
      label: "Resolve user + groups",
      title: "The gateway looks the user up in Cloud Identity Engine",
      why: "CIE syncs users and groups from your directory, so the gateway can match the token's user to an authoritative record.",
      code: "lookup upn=dev@company.com",
      holds: "Access token and refresh token",
      diff: "New directory lookup. CIE syncs about every 5 minutes, so new users can take up to 5 minutes to resolve.",
    },
    {
      from: CIE, to: GW, phase: "Tool call",
      label: "User record + groups",
      title: "CIE returns the current directory record",
      why: "These groups reflect the directory now, not when you signed in.",
      code: '{"user":"dev@company.com",\n "status":"active",\n "groups":["eng-platform","db-readers"]}',
      holds: "Access token and refresh token",
      diff: "Group membership is current to the last sync, not frozen at sign-in.",
    },
    {
      from: GW, to: GW, phase: "Tool call",
      label: "Check token + directory policy",
      title: "The gateway authorizes using directory groups",
      why: "Same server and tool checks as scenario 1, but driven by CIE groups. A user disabled or removed from a group is denied on the next call after sync, even with a valid token.",
      code: 'token  signature, expiry, audience           ok\nuser   active in CIE                         ok\ngroups eng-platform, db-readers (from CIE)\ntool   "read_query" allowed for db-readers?  ok',
      holds: "Access token and refresh token",
      diff: "Policy uses directory groups, so revocation no longer waits for the token to expire.",
    },
    ...s1.slice(12),
  ];
}

function buildS3(c: Client): Step[] {
  const s1 = buildS1(c);
  const s2 = buildS2(c);
  const reg = "Registered client ID and secret";
  const tok = "Short-lived user + agent token";
  return [
    { ...s1[0], holds: reg },
    { ...s1[1], holds: reg },
    {
      ...s1[2],
      code: "GET /.well-known/oauth-protected-resource\nGET /.well-known/oauth-authorization-server\n  -> authorization_endpoint, token_endpoint",
      holds: reg,
    },
    {
      from: CLIENT_LANE, to: CLIENT_LANE, phase: "Discovery",
      label: "Use registered agent ID",
      title: `${c.short} uses credentials from its agent registration`,
      why: `An admin registered ${c.name} in Agent Identity Security, which produced an Agent ID plus an OAuth 2.1 client ID and secret. The secret is stored in: ${c.secretStore}.`,
      code: c.configS3,
      holds: reg,
      diff: `Replaces dynamic client registration. ${c.s3Note}`,
    },
    {
      ...s1[4],
      code: `GET /oauth/authorize?response_type=code\n  &client_id=${c.agentId}\n  &code_challenge=SHA256(verifier)\n  &code_challenge_method=S256`,
      holds: "Client ID, secret, PKCE verifier",
    },
    {
      from: GW, to: IDP, phase: "Sign-in",
      label: "Redirect to Idira, then IdP",
      title: "The gateway sends your browser to Idira, which federates to your IdP",
      why: "Idira is Palo Alto's sign-in service for Agent Identity Security. It must be federated with the same IdP that CIE syncs from.",
      code: "302 Found\nLocation: https://<idira>/login?...\n  -> federates to Okta or Entra ID",
      holds: "Client ID, secret, PKCE verifier",
      diff: "User sign-in goes through Idira rather than directly from the gateway to the IdP.",
    },
    { ...s2[6], diff: undefined, holds: "Client ID, secret, PKCE verifier" },
    {
      from: GW, to: MCP, phase: "Sign-in",
      label: "Optional: MCP server OAuth",
      title: "If the MCP server has its own OAuth, you consent to it too",
      why: "When the registered server uses an OAuth 2.1 authorization code flow, the gateway asks for consent and runs that second flow. The resulting token stays with the gateway.",
      code: `Gateway -> consent screen for <server>\nBrowser -> MCP server's authorization server\nToken stored by gateway, never sent to ${c.short}`,
      holds: "Client ID, secret, PKCE verifier",
      diff: "Agent Identity Security documents upstream consent inside the sign-in flow, after the access policy check passes.",
    },
    { ...s1[7] },
    {
      from: CLIENT_LANE, to: GW, phase: "Token",
      label: "Code + verifier + secret",
      title: `${c.short} redeems the code as an authenticated agent`,
      why: "The token request proves which registered agent is asking, using the client secret, as well as PKCE.",
      code: `POST /oauth/token\ngrant_type=authorization_code\n&code=xyz789\n&code_verifier=<original secret>\n&client_id=${c.agentId}\n&client_secret=<agent secret>`,
      holds: "One-time auth code",
      diff: "Confidential client: the agent's client secret is required at the token endpoint.",
    },
    {
      from: GW, to: CLIENT_LANE, phase: "Token",
      label: "Short-lived user + agent token",
      title: `The gateway issues a token for you acting through ${c.short}`,
      why: "The token carries two identities: the user who signed in and the agent they are using.",
      code: `{"access_token":"eyJ...",\n "token_type":"Bearer",\n "expires_in":900}\n\nsub   dev@company.com\nagent ${c.agentId}`,
      holds: tok,
      diff: "Token represents a user-agent pair, not just a user.",
    },
    { ...s1[10], holds: tok },
    { ...s2[11], diff: undefined, holds: tok },
    { ...s2[12], diff: undefined, holds: tok },
    {
      from: GW, to: GW, phase: "Tool call",
      label: "User-agent-tool policy",
      title: "The gateway checks workspace and user-agent-tool policy",
      why: `Authorization asks: are ${c.short}'s agent, the user and the server in the same workspace, and does a policy let this user, through ${c.short}, call this tool? Each client can be its own agent with different rights.`,
      code: `token     signature, expiry, audience           ok\nworkspace agent + user + server share eng-platform  ok\npolicy    dev@company.com via ${c.short}\n          -> <server> tool "read_query"         allow`,
      holds: tok,
      diff: "Policies are user + agent + tool, defined under AI Gateway > Agent Identity > Policies, and require a shared workspace.",
    },
    { ...s1[12], holds: tok },
    { ...s1[13], holds: tok },
    {
      from: GW, to: CLIENT_LANE, phase: "Tool call",
      label: "Result + dual-attribution log",
      title: "The gateway returns the result and logs both identities",
      why: `Every action is recorded with the agent identity and the user identity, so you can tell what a person did versus what ${c.short} did for them.`,
      code: `audit  user=dev@company.com  agent=${c.agentId}\n       server=<server-slug>  tool=read_query  outcome=allow`,
      holds: tok,
      diff: "Dual attribution: user and agent on every log entry.",
    },
  ];
}

const SCENARIOS: Record<ScenarioId, Scenario> = {
  gwIdp: {
    name: "1. AI GW + IdP",
    heading: "Scenario 1: AI Gateway with direct IdP sign-in",
    intro: "The gateway federates sign-in to your IdP and trusts the groups in the token. No directory sync and no agent identity.",
    unusedLanes: [CIE],
    build: buildS1,
    takeaways: (c) => [
      { title: "Authentication: user only", body: `Steps 5 to 10. Your IdP proves who you are. ${c.short} is an anonymous OAuth client protected only by PKCE.` },
      { title: "Authorization: token groups", body: "Step 12. Groups captured at sign-in decide which servers and tools you may use, until you sign in again." },
      { title: "Upstream secrets stay in the gateway", body: "Step 13. Your token stops at the gateway; it uses its own stored credential for the MCP server." },
    ],
    callout: () => ({
      title: "Revoking access",
      body: "Disabling a user in the IdP stops new sign-ins, but an existing access token works until it expires or the gateway revokes it. Keep token lifetimes short.",
    }),
  },
  gwCieIdp: {
    name: "2. AI GW + CIE + IdP",
    heading: "Scenario 2: AI Gateway with Cloud Identity Engine",
    intro: "Same sign-in as scenario 1, but the gateway reads users and groups from Cloud Identity Engine on each call. Dots mark what is new versus scenario 1.",
    unusedLanes: [],
    build: buildS2,
    takeaways: (c) => [
      { title: "Authentication: user only", body: `Steps 5 to 10. Same IdP sign-in; ${c.short} is still a public OAuth client.` },
      { title: "Authorization: directory groups", body: "Steps 12 to 14. CIE supplies current users and groups, so policy follows the directory rather than a frozen token." },
      { title: "Upstream secrets stay in the gateway", body: "Step 15. Unchanged: the MCP server sees only the gateway's credential." },
    ],
    callout: () => ({
      title: "Revoking access",
      body: "Disable the user or remove them from the group in your directory. After the next CIE sync (about 5 minutes), calls are denied even if the token has not expired.",
    }),
  },
  gwCieAisIdp: {
    name: "3. AI GW + CIE + AIS + IdP",
    heading: "Scenario 3: AI Gateway with CIE and Agent Identity Security",
    intro: "Adds a registered identity for the coding agent itself. Sign-in goes through Idira, tokens bind user and agent, and policies are user + agent + tool. Dots mark what is new versus scenario 2.",
    unusedLanes: [],
    build: buildS3,
    takeaways: (c) => [
      { title: "Authentication: user and agent", body: `Steps 4 to 11. You sign in through Idira and your IdP; ${c.short} proves it is the registered agent with its client secret.` },
      { title: "Authorization: user + agent + tool", body: `Steps 13 to 15. CIE resolves your groups, then workspace membership and access policies decide what you may do through ${c.short}.` },
      { title: "Upstream secrets stay in the gateway", body: "Steps 8 and 16. Optional upstream OAuth consent and stored credentials. Your token is never forwarded." },
    ],
    callout: (c) => ({
      title: "Revoking access",
      body: `Disable the user in the directory (effective after CIE sync), remove the access policy, or delete the ${c.short} agent registration to cut off that tool for everyone while leaving the other clients working.`,
    }),
  },
};

const SCENARIO_ROWS: string[][] = [
  ["Who is authenticated", "User", "User", "User and agent"],
  ["How the client identifies itself", "Dynamic registration, public client", "Dynamic registration, public client", "Pre-registered Agent ID and client secret"],
  ["User sign-in path", "Gateway SSO to IdP", "Gateway SSO to IdP", "Idira federated to IdP"],
  ["Source of groups", "Token claims at sign-in", "CIE directory sync", "CIE directory sync"],
  ["Policy shape", "Group to server and tool", "Directory group to server and tool", "User + agent to server and tool, shared workspace"],
  ["Disabling a user", "Effective when token expires", "Effective after CIE sync, about 5 min", "Same as 2, plus per-agent cut-off"],
  ["Audit log identity", "User", "User", "User and agent"],
  ["Added setup", "IdP app for gateway SSO", "CIE tenant, matching user attribute", "Idira federation, agent registration per client, access policies, add-on licence"],
];

const SCENARIO_PROS_CONS: { pros: string[]; cons: string[]; fixes: string[] }[] = [
  {
    pros: [
      "Simplest setup with the fewest components",
      "Standard OAuth that works with every client",
    ],
    cons: [
      "Groups are stale until the user signs in again",
      "Revocation waits for the token to expire",
      "Cannot tell which tool made a call",
    ],
    fixes: [
      "Keep access tokens short-lived (for example 15 to 60 minutes) so refreshed tokens pick up new groups",
      "Short token lifetimes plus revoking refresh tokens at the gateway and IdP when a user leaves",
      "Move to scenario 3, or in the meantime log the client's User-Agent at the gateway as a hint",
    ],
  },
  {
    pros: [
      "Policy follows the live directory",
      "Disabled users blocked within about 5 minutes",
      "No group claims needed in tokens",
    ],
    cons: [
      "Extra dependency on CIE and attribute mapping",
      "New users wait up to 5 minutes for access",
      "Still no agent identity or per-tool-client rules",
    ],
    fixes: [
      "Monitor CIE sync health and alert on failures; test the user attribute mapping before rollout",
      "Trigger an on-demand CIE sync as part of onboarding, or provision users a few minutes before their start",
      "Add Agent Identity Security (scenario 3) when per-client rules or agent audit are needed",
    ],
  },
  {
    pros: [
      "Different rights per client, for example Codex read-only",
      "Audit logs show both user and agent",
      "Cut off one client for everyone in one step",
      "Short-lived tokens",
    ],
    cons: [
      "Add-on licence and the most setup",
      "A client secret on every laptop is a weak barrier",
      "Redirect URIs and registration to manage per client",
      "Static credential support varies by client version",
    ],
    fixes: [
      "Start with one high-value client and server, then expand; automate agent registration with scripts or infrastructure as code",
      "Treat the secret as a client label, not a user credential: rely on user SSO plus policy, store it in the OS keychain, and rotate it",
      "Keep a per-client registry of redirect URIs and pinned versions, and test each client upgrade before rollout",
      "Pin client versions through managed settings or MDM, and fall back to scenario 2 for clients that can't use static credentials",
    ],
  },
];

function ProsCons({ pros, cons }: { pros: string[]; cons: string[] }) {
  const theme = useHostTheme();
  const list = (items: string[], marker: string, color: string) => (
    <Stack gap={4}>
      {items.map((item) => (
        <Row key={item} gap={6} align="start">
          <span style={{ color, fontWeight: 600, width: 10, flexShrink: 0 }}>{marker}</span>
          <Text size="small">{item}</Text>
        </Row>
      ))}
    </Stack>
  );
  return (
    <Stack gap={10}>
      <Stack gap={4}>
        <Text size="small" tone="tertiary">Positives</Text>
        {list(pros, "+", theme.text.secondary)}
      </Stack>
      <Stack gap={4}>
        <Text size="small" tone="tertiary">Negatives</Text>
        {list(cons, "-", theme.text.secondary)}
      </Stack>
    </Stack>
  );
}

function Remediations({ cons, fixes }: { cons: string[]; fixes: string[] }) {
  return (
    <Stack gap={10}>
      {fixes.map((fix, i) => (
        <Stack key={fix} gap={2}>
          <Text size="small" tone="tertiary">{cons[i]}</Text>
          <Text size="small">{fix}</Text>
        </Stack>
      ))}
    </Stack>
  );
}

function CodeBlock({ children }: { children: string }) {
  const theme = useHostTheme();
  return (
    <pre
      style={{
        margin: 0,
        padding: 12,
        borderRadius: 6,
        background: theme.fill.tertiary,
        color: theme.text.primary,
        fontSize: 12,
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontFamily: "var(--vscode-editor-font-family, monospace)",
      }}
    >
      {children}
    </pre>
  );
}

function SequenceDiagram({ lanes, steps, unusedLanes, current, onSelect }: { lanes: string[]; steps: Step[]; unusedLanes: number[]; current: number; onSelect: (i: number) => void }) {
  const theme = useHostTheme();
  const W = 640;
  const headH = 40;
  const top = headH + 16;
  const rowH = 34;
  const H = top + steps.length * rowH + 6;
  const laneW = W / lanes.length;
  const xs = lanes.map((_, i) => laneW * (i + 0.5));

  const colorFor = (i: number) =>
    i === current ? theme.accent.primary : i < current ? theme.text.secondary : theme.stroke.secondary;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} role="img" aria-label={`Sequence between ${lanes.join(", ")}`}>
      <defs>
        {[
          ["arrowActive", theme.accent.primary],
          ["arrowPast", theme.text.secondary],
          ["arrowFuture", theme.stroke.secondary],
        ].map(([id, fill]) => (
          <marker key={id} id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={fill} />
          </marker>
        ))}
      </defs>

      {lanes.map((name, i) => {
        const unused = unusedLanes.includes(i);
        const bw = Math.min(laneW - 10, name.length * 7.4 + 22);
        return (
          <g key={`${i}-${name}`}>
            <rect
              x={xs[i] - bw / 2}
              y={0}
              width={bw}
              height={headH}
              rx={6}
              fill={unused ? "transparent" : i === CLIENT_LANE ? theme.fill.tertiary : theme.fill.secondary}
              stroke={unused ? theme.stroke.tertiary : i === CLIENT_LANE ? theme.accent.primary : theme.stroke.primary}
              strokeDasharray={unused ? "4 3" : undefined}
            />
            <text x={xs[i]} y={unused ? headH / 2 - 7 : headH / 2} textAnchor="middle" dominantBaseline="central" fontSize={12} fill={unused ? theme.text.tertiary : theme.text.primary}>
              {name}
            </text>
            {unused ? (
              <text x={xs[i]} y={headH / 2 + 9} textAnchor="middle" dominantBaseline="central" fontSize={10} fill={theme.text.tertiary}>
                not used
              </text>
            ) : (
              <line x1={xs[i]} y1={headH} x2={xs[i]} y2={H} stroke={theme.stroke.tertiary} strokeDasharray="3 4" />
            )}
          </g>
        );
      })}

      {steps.map((s, i) => {
        const y = top + i * rowH + 24;
        const color = colorFor(i);
        const marker = i === current ? "arrowActive" : i < current ? "arrowPast" : "arrowFuture";
        const active = i === current;
        const label = `${i + 1}  ${s.label}`;
        const tw = label.length * 6.3;
        const textStyle = {
          paintOrder: "stroke" as const,
          stroke: theme.bg.editor,
          strokeWidth: 4,
          strokeLinejoin: "round" as const,
        };
        const textFill = active ? theme.text.primary : i < current ? theme.text.secondary : theme.text.tertiary;

        let shape;
        let lx: number;
        let ly: number;
        let anchor: "middle" | "start" = "middle";
        if (s.from === s.to) {
          const x0 = xs[s.from];
          shape = (
            <path d={`M${x0},${y - 8} h26 v12 h-23`} fill="none" stroke={color} strokeWidth={active ? 2 : 1} markerEnd={`url(#${marker})`} />
          );
          lx = Math.min(x0 + 32, W - tw - 2);
          ly = y - 2;
          anchor = "start";
        } else {
          const x1 = xs[s.from];
          const x2 = xs[s.to];
          const dir = x2 > x1 ? 1 : -1;
          shape = (
            <line x1={x1 + dir * 2} y1={y} x2={x2 - dir * 3} y2={y} stroke={color} strokeWidth={active ? 2 : 1} markerEnd={`url(#${marker})`} />
          );
          lx = Math.max(tw / 2 + 2, Math.min(W - tw / 2 - 2, (x1 + x2) / 2));
          ly = y - 10;
        }

        return (
          <g key={i} onClick={() => onSelect(i)} style={{ cursor: "pointer" }}>
            <rect x={0} y={top + i * rowH} width={W} height={rowH} fill={active ? theme.fill.quaternary : "transparent"} />
            {shape}
            {s.diff ? <circle cx={6} cy={y - (s.from === s.to ? 2 : 10)} r={3} fill={theme.accent.primary} /> : null}
            <text x={lx} y={ly} textAnchor={anchor} dominantBaseline="central" fontSize={11.5} fontWeight={active ? 600 : 400} fill={textFill} style={textStyle}>
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function StepDetail({ steps, index, setIndex }: { steps: Step[]; index: number; setIndex: (i: number) => void }) {
  const s = steps[index];
  return (
    <Stack gap={14}>
      <Row justify="space-between" align="center">
        <Row gap={8} align="center">
          <Text weight="medium">Step {index + 1} of {steps.length}</Text>
          <Pill size="sm" tone="info">{s.phase}</Pill>
        </Row>
        <Row gap={6}>
          <Button variant="secondary" disabled={index === 0} onClick={() => setIndex(index - 1)}>Back</Button>
          <Button variant="primary" disabled={index === steps.length - 1} onClick={() => setIndex(index + 1)}>Next</Button>
        </Row>
      </Row>

      <H3>{s.title}</H3>
      <Text tone="secondary">{s.why}</Text>

      {s.diff ? (
        <Callout tone="warning" title="New in this scenario">
          {s.diff}
        </Callout>
      ) : null}

      <Stack gap={6}>
        <Text size="small" tone="tertiary">Example (illustrative values)</Text>
        <CodeBlock>{s.code}</CodeBlock>
      </Stack>

      <Row gap={8} align="center" wrap>
        <Text size="small" tone="tertiary">Client now holds</Text>
        <Code>{s.holds}</Code>
      </Row>
    </Stack>
  );
}

function ClientSetup({ client, scenarioId }: { client: Client; scenarioId: ScenarioId }) {
  const isS3 = scenarioId === "gwCieAisIdp";
  const facts: [string, string][] = [
    ["Config location", client.configFile],
    ["Start sign-in", client.trigger.charAt(0).toUpperCase() + client.trigger.slice(1)],
    ["Client registration", isS3 ? `Static client ID ${client.agentId}` : client.registration],
    ["Client secret stored in", isS3 ? client.secretStore : "Not used (public client)"],
    ["OAuth redirect URI", client.redirect],
    ["Account login", client.accountNote],
  ];
  return (
    <Card>
      <CardHeader trailing={<Pill size="sm">{isS3 ? "Static agent credentials" : "Automatic registration"}</Pill>}>
        {client.name} setup for this scenario
      </CardHeader>
      <CardBody>
        <Grid columns="minmax(0, 1fr) minmax(0, 1fr)" gap={20} align="start">
          <CodeBlock>{isS3 ? client.configS3 : client.configS12}</CodeBlock>
          <Stack gap={10}>
            {facts.map(([k, v]) => (
              <Stack key={k} gap={2}>
                <Text size="small" tone="tertiary">{k}</Text>
                <Text size="small">{v}</Text>
              </Stack>
            ))}
          </Stack>
        </Grid>
      </CardBody>
    </Card>
  );
}

export default function AiGatewayMcpAuthScenarios() {
  const [clientId, setClientId] = useState<ClientId>("claude");
  const [scenarioId, setScenarioId] = useState<ScenarioId>("gwIdp");
  const [index, setIndex] = useState(0);
  const client = CLIENTS[clientId];
  const scenario = SCENARIOS[scenarioId];
  const steps = scenario.build(client);
  const safeIndex = Math.min(index, steps.length - 1);
  const current = steps[safeIndex];
  const lanes = [client.short, "AI Gateway", "Enterprise IdP", "CIE", "MCP server"];
  const callout = scenario.callout(client);

  return (
    <Stack gap={20} style={{ padding: 20 }}>
      <Stack gap={10}>
        <Row gap={8} wrap align="center">
          <Text size="small" tone="tertiary" style={{ width: 60 }}>Client</Text>
          {(Object.keys(CLIENTS) as ClientId[]).map((id) => (
            <Pill key={id} active={clientId === id} onClick={() => setClientId(id)}>
              {CLIENTS[id].name}
            </Pill>
          ))}
        </Row>
        <Row gap={8} wrap align="center">
          <Text size="small" tone="tertiary" style={{ width: 60 }}>Scenario</Text>
          {(Object.keys(SCENARIOS) as ScenarioId[]).map((id) => (
            <Pill
              key={id}
              active={scenarioId === id}
              onClick={() => {
                setScenarioId(id);
                setIndex(0);
              }}
            >
              {SCENARIOS[id].name}
            </Pill>
          ))}
        </Row>
      </Stack>

      <Stack gap={6}>
        <H1>{scenario.heading} with {client.name}</H1>
        <Text tone="secondary">{scenario.intro}</Text>
      </Stack>

      <Row gap={6} wrap align="center">
        <Text size="small" tone="tertiary">Jump to</Text>
        {PHASES.map((p) => {
          const first = steps.findIndex((s) => s.phase === p);
          return (
            <Pill key={p} size="sm" active={current.phase === p} onClick={() => setIndex(first)}>
              {p}
            </Pill>
          );
        })}
        <Text size="small" tone="tertiary">Dot marks steps new in this scenario</Text>
      </Row>

      <Grid columns="minmax(0, 1.35fr) minmax(0, 1fr)" gap={20} align="start">
        <SequenceDiagram lanes={lanes} steps={steps} unusedLanes={scenario.unusedLanes} current={safeIndex} onSelect={setIndex} />
        <Card>
          <CardBody>
            <StepDetail steps={steps} index={safeIndex} setIndex={setIndex} />
          </CardBody>
        </Card>
      </Grid>

      <ClientSetup client={client} scenarioId={scenarioId} />

      <Grid columns={3} gap={20}>
        {scenario.takeaways(client).map((t) => (
          <Stack key={t.title} gap={6}>
            <Text weight="medium">{t.title}</Text>
            <Text size="small" tone="secondary">{t.body}</Text>
          </Stack>
        ))}
      </Grid>

      <Callout tone="info" title={callout.title}>
        {callout.body}
      </Callout>

      <Divider />

      <Stack gap={10}>
        <H2>How the three clients differ</H2>
        <Table
          headers={["", CLIENTS.claude.name, CLIENTS.codex.name, CLIENTS.vscode.name]}
          rows={[
            ["Config location", CLIENTS.claude.configFile, CLIENTS.codex.configFile, CLIENTS.vscode.configFile],
            ["Start sign-in", CLIENTS.claude.trigger, CLIENTS.codex.trigger, CLIENTS.vscode.trigger],
            ["Default registration", CLIENTS.claude.registration, CLIENTS.codex.registration, CLIENTS.vscode.registration],
            ["Static client ID and secret", CLIENTS.claude.staticSupport, CLIENTS.codex.staticSupport, CLIENTS.vscode.staticSupport],
            ["Client secret storage", CLIENTS.claude.secretStore, CLIENTS.codex.secretStore, CLIENTS.vscode.secretStore],
            ["OAuth redirect URI", CLIENTS.claude.redirect, CLIENTS.codex.redirect, CLIENTS.vscode.redirect],
            ["Org-level MCP control", CLIENTS.claude.orgControl, CLIENTS.codex.orgControl, CLIENTS.vscode.orgControl],
          ]
            .map((r): Cell[] => [<Text key="k" weight="medium">{r[0]}</Text>, r[1], r[2], r[3]])
            .concat([
              [
                <Text key="k" weight="medium">Positives and negatives</Text>,
                <ProsCons key="c" pros={CLIENTS.claude.pros} cons={CLIENTS.claude.cons} />,
                <ProsCons key="x" pros={CLIENTS.codex.pros} cons={CLIENTS.codex.cons} />,
                <ProsCons key="v" pros={CLIENTS.vscode.pros} cons={CLIENTS.vscode.cons} />,
              ],
              [
                <Text key="k" weight="medium">Remediation for negatives</Text>,
                <Remediations key="c" cons={CLIENTS.claude.cons} fixes={CLIENTS.claude.fixes} />,
                <Remediations key="x" cons={CLIENTS.codex.cons} fixes={CLIENTS.codex.fixes} />,
                <Remediations key="v" cons={CLIENTS.vscode.cons} fixes={CLIENTS.vscode.fixes} />,
              ],
            ])}
        />
      </Stack>

      <Stack gap={10}>
        <H2>How the three scenarios differ</H2>
        <Table
          headers={["", SCENARIOS.gwIdp.name, SCENARIOS.gwCieIdp.name, SCENARIOS.gwCieAisIdp.name]}
          rows={SCENARIO_ROWS.map((r): Cell[] => [<Text key="k" weight="medium">{r[0]}</Text>, r[1], r[2], r[3]]).concat([
            [
              <Text key="k" weight="medium">Positives and negatives</Text>,
              ...SCENARIO_PROS_CONS.map((pc, i) => <ProsCons key={i} pros={pc.pros} cons={pc.cons} />),
            ],
            [
              <Text key="k" weight="medium">Remediation for negatives</Text>,
              ...SCENARIO_PROS_CONS.map((pc, i) => <Remediations key={i} cons={pc.cons} fixes={pc.fixes} />),
            ],
          ])}
        />
        <Text size="small" tone="tertiary">
          Client details come from current Claude Code, Codex and VS Code MCP docs. Scenario 3 follows Palo Alto's Agent Identity Security docs; scenarios 1 and 2 reflect the gateway's SSO and CIE options. Confirm exact settings in Strata Cloud Manager.
        </Text>
      </Stack>
    </Stack>
  );
}
