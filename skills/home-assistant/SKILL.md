# Home Assistant Assist

Control Home Assistant smart home devices using the Assist (Conversation) API by passing natural language directly to Home Assistant's built-in NLU for fast, token-efficient control.

---
name: homeassistant-assist
version: 0.1.1
description: Control Home Assistant smart home devices using the Assist (Conversation) API. Use this skill when the user wants to control smart home entities - lights, switches, thermostats, covers, vacuums, media players, or any other smart device. Passes natural language directly to Home Assistant's built-in NLU for fast, token-efficient control.
homepage: https://github.com/DevelopmentCats/homeassistant-assist
metadata:
  openclaw:
    emoji: "🏠"
    requires:
      bins: ["curl"]
      env: ["HASS_SERVER", "HASS_TOKEN"]
    primaryEnv: "HASS_TOKEN"
  autobot:
    credential: "homeassistant"
    base_url_source: "user configuration or HASS_SERVER"
    preferred_tools:
      - "homeassistant-assist-token"
      - "homeassistant-entity-search-token"
    compatibility: "Autobot credential-resolution compatibility release"
---

# Home Assistant Assist

Control smart home devices by passing natural language to Home Assistant's Assist (Conversation) API. **Fire and forget** — trust Assist to handle intent parsing, entity resolution, and execution.

## Version

**Current version:** `0.1.1`

### Changelog

#### 0.1.1

**Fixed**
- Documented Autobot credential usage through the stored `homeassistant` credential.
- Documented the validated in-memory token flow using `get_credential("homeassistant")` plus `homeassistant-assist-token`.
- Added Autobot compatibility metadata for the credential name and preferred tools.
- Clarified that token handling must stay in memory and must not be exposed through shell commands, logs, or workspace files.

**Notes**
- This is an Autobot compatibility patch release.
- No Home Assistant API behavior changed.
- The upstream OpenClaw environment-variable flow remains documented for non-Autobot usage.

## When to Use This Skill

Use this skill when the user wants to **control or query any smart home device**. If it's in Home Assistant, Assist can handle it.

Examples:
- Turn lights, switches, or scenes on/off.
- Query entity state.
- Control covers, thermostats, vacuums, media players, or other Home Assistant entities.
- Search entities before issuing a command when the user asks for a list or when target names are ambiguous.

## How It Works

Pass the user's request directly to Assist:

```bash
curl -s -X POST "$HASS_SERVER/api/conversation/process" \
  -H "Authorization: Bearer $HASS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "USER REQUEST HERE", "language": "en"}'
```

**Trust Assist.** It handles intent parsing, fuzzy entity matching, area-aware commands, execution, and error responses.

## Autobot Usage

In Autobot, prefer tool-based execution over shell commands so credentials never appear in process arguments, logs, or files.

### Credential

Use the stored credential `homeassistant`. The credential must contain a Home Assistant long-lived access token.

### Preferred control flow

1. Fetch the token with `get_credential("homeassistant")`.
2. Pass the token in memory to `homeassistant-assist-token`.
3. Relay Home Assistant's response to the user.

### Entity search flow

When the user asks to list or find devices, use `homeassistant-entity-search-token`. Recommended filters include `domain: "light"`, `domain: "switch"`, and a `query` matching the room or device name.

### Endpoint configuration

Use the Home Assistant base URL configured by the user or workspace, for example via `HASS_SERVER`. Do not hard-code private deployment URLs in reusable templates.

## Setup

### OpenClaw / environment-variable setup

```json
{
  "env": {
    "HASS_SERVER": "https://your-homeassistant-url",
    "HASS_TOKEN": "your-long-lived-access-token"
  }
}
```

### Autobot setup

Store a Home Assistant long-lived access token as an agent-scoped or global credential named `homeassistant`.

Generate a token: Home Assistant → Profile → Long-Lived Access Tokens → Create Token

## API Reference

Endpoint: `POST /api/conversation/process`

Request:

```json
{
  "text": "turn on the kitchen lights",
  "language": "en"
}
```

## Security Notes

- Never print, echo, log, or store the Home Assistant token.
- Do not place the token in shell commands if an in-memory tool is available.
- Prefer `homeassistant-assist-token` and `homeassistant-entity-search-token` in Autobot.
- If a wrapper reports a missing credential, validate with `get_credential("homeassistant")` and use the token-based tool variant as a fallback.
