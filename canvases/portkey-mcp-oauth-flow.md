# AI Gateway MCP Auth Scenarios

Markdown companion for `canvases/portkey-mcp-oauth-flow.canvas.tsx`.

## What this covers

This document explains three AI Gateway authentication and authorization scenarios for MCP access, compared across three clients:

- Claude Code
- Codex
- GitHub Copilot in VS Code

It is the static Markdown version of the interactive canvas.

## Three scenarios at a glance

### 1) AI Gateway + IdP

- **Authentication:** User signs in through gateway SSO to the enterprise IdP.
- **Authorization source:** Group claims in the token from sign-in time.
- **Strength:** Simplest setup.
- **Tradeoff:** Group changes are not reflected until token refresh/sign-in.

### 2) AI Gateway + CIE + IdP

- **Authentication:** Same user sign-in path as Scenario 1.
- **Authorization source:** Cloud Identity Engine (directory-synced groups) on tool call.
- **Strength:** Policy follows directory state with near-real-time sync.
- **Tradeoff:** Adds CIE dependency and sync timing considerations.

### 3) AI Gateway + CIE + AIS + IdP

- **Authentication:** User + registered agent identity (client tool identity).
- **Authorization source:** User + agent + tool policy with shared workspace checks.
- **Strength:** Per-client access control and dual attribution logs.
- **Tradeoff:** Highest setup complexity and lifecycle management overhead.

## Client comparison

| Topic | Claude Code | Codex | GitHub Copilot in VS Code |
|---|---|---|---|
| Config location | `~/.claude.json` or `.mcp.json` | `~/.codex/config.toml` or project `.codex/config.toml` | `.vscode/mcp.json` or user profile `mcp.json` |
| Default registration | Dynamic registration / metadata document | Metadata document or dynamic registration | Dynamic registration when available; otherwise prompt for static client config |
| Static client ID + secret support | Yes | Yes | Yes |
| Secret storage | System keychain | Plain text in `config.toml` | VS Code secret storage (OS-backed) |
| Org-level MCP control | Managed settings can restrict servers | Check current Codex admin controls | Copilot Business/Enterprise policies can restrict MCP |

### Positives, negatives, and remediation

#### Claude Code

- **Positives**
  - Secret kept in keychain.
  - Supports fixed callback port.
  - Managed settings can restrict servers.
- **Negatives**
  - Requests advertised scopes broadly.
  - Redirect regression risk across versions.
  - User-by-user setup flow.
- **Remediation**
  - Limit scopes at the gateway policy/token issuance level.
  - Pin known-good client version and register allowed callback variants.
  - Provide project-scoped bootstrap config and scripted onboarding.

#### Codex

- **Positives**
  - Supports metadata document and dynamic registration.
  - Headless login option.
  - Env-var token patterns keep shared config cleaner.
- **Negatives**
  - Static secret can live in plaintext config.
  - Less explicit org-level MCP governance guidance.
  - Callback URL must match exactly.
- **Remediation**
  - Prefer dynamic registration/metadata document; otherwise lock file perms, never commit secrets, rotate regularly.
  - Enforce allowlists and policy at the gateway.
  - Standardize callback port/URL and register exact values.

#### GitHub Copilot in VS Code

- **Positives**
  - Secret stored in OS-backed secret storage.
  - Enterprise policy controls are available.
  - Works with static client ID when needed.
- **Negatives**
  - Some versions may favor dynamic registration.
  - Redirect URI behavior can vary by version.
  - Workspace `mcp.json` can be accidentally committed.
- **Remediation**
  - Validate behavior on pinned versions and enforce gateway-side registration expectations.
  - Capture real redirect URIs from test sign-in and register exact URIs.
  - Keep only non-secret identifiers in workspace files; add secret scanning in CI/pre-commit.

## Scenario comparison

| Topic | 1. AI GW + IdP | 2. AI GW + CIE + IdP | 3. AI GW + CIE + AIS + IdP |
|---|---|---|---|
| Who is authenticated | User | User | User + Agent |
| Client identity model | Dynamic/public | Dynamic/public | Pre-registered agent + client secret |
| Group source for policy | Token claims | CIE directory sync | CIE directory sync |
| Policy shape | Group -> server/tool | Directory group -> server/tool | User + agent + tool |
| Audit attribution | User | User | User + agent |

### Positives, negatives, and remediation

#### Scenario 1 (AI GW + IdP)

- **Positives**
  - Fastest to implement.
  - Standard OAuth path.
- **Negatives**
  - Group data can go stale.
  - Revocation often waits for token lifetime boundaries.
  - Limited tool-level attribution by client.
- **Remediation**
  - Use short-lived access tokens.
  - Revoke refresh tokens when users leave.
  - Move to Scenario 3 where client-level attribution is required.

#### Scenario 2 (AI GW + CIE + IdP)

- **Positives**
  - Directory-driven policy decisions.
  - Faster revocation effect after sync.
  - Reduces dependency on token-embedded group claims.
- **Negatives**
  - Introduces CIE mapping/sync dependency.
  - New-user access can wait for sync.
  - Still no true per-client agent identity policy.
- **Remediation**
  - Monitor sync health and validate identity attribute mapping.
  - Pre-provision and/or trigger sync during onboarding windows.
  - Adopt Scenario 3 when per-tool-client policy is needed.

#### Scenario 3 (AI GW + CIE + AIS + IdP)

- **Positives**
  - Granular per-client rights.
  - Dual attribution in logs (user + agent).
  - Faster targeted cut-off by disabling agent registration/policy.
- **Negatives**
  - Highest operational and licensing overhead.
  - Client secret footprint exists on endpoints.
  - Redirect URI and version compatibility management overhead.
- **Remediation**
  - Roll out incrementally with automation for registration/policy.
  - Treat client secret as identifier support, not user auth replacement; rely on user SSO + policy and rotate secrets.
  - Maintain version pinning and compatibility test matrix per client.

## Notes

- Keep MCP backend credentials at the gateway; do not forward user access tokens upstream.
- Validate exact redirect URIs and behavior against your current client versions.
- Confirm policy and identity settings in your production gateway control plane before rollout.

