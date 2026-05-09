# Home Assistant Assist

Control Home Assistant smart home devices using the Assist (Conversation) API by passing natural language directly to Home Assistant's built-in NLU for fast, token-efficient control.

---
name: homeassistant-assist
version: 0.1.1
description: Control Home Assistant smart home devices using the Assist (Conversation) API. Use this skill when the user wants to control smart home entities - lights, switches, thermostats, covers, vacuums, media players, or any other smart device. Passes natural language directly to Home Assistant's built-in NLU for fast, token-efficient control.
metadata:
  autobot:
    credential: "homeassistant"
    base_url_source: "user configuration or HASS_SERVER"
    preferred_tools:
      - "homeassistant-assist-token"
      - "homeassistant-entity-search-token"
    compatibility: "Autobot credential-resolution compatibility release"
---

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

#### 0.1.0

Initial release.

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
# Build the JSON payload safely to avoid shell injection from user input
PAYLOAD=$(jq -n --arg text "USER REQUEST HERE" --arg lang "en" '{text: $text, language: $lang}')
curl -s -X POST "$HASS_SERVER/api/conversation/process" \
  -H "Authorization: Bearer $HASS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"
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

Use the Home Assistant base URL configured by the user or workspace. Do not hard-code private deployment URLs in reusable templates.

## Setup

Store your Home Assistant credentials using the platform credential store:

```
set_credential(name="homeassistant", value="your-long-lived-access-token", description="Home Assistant long-lived access token")
set_credential(name="hass_server", value="https://your-homeassistant-url", description="Home Assistant base URL")
```

Then retrieve them at runtime:

```
HASS_TOKEN = get_credential("homeassistant")["value"]
HASS_SERVER = get_credential("hass_server")["value"]
```

Generate a token: Home Assistant → Profile → Long-Lived Access Tokens → Create Token

## Handling Responses

**Just relay what Assist says.** The `response.speech.plain.speech` field contains the human-readable result.

- `"Turned on the light"` → Success, tell the user
- `"Sorry, I couldn't understand that"` → Assist couldn't parse it
- `"Sorry, there are multiple devices called X"` → Ambiguous name

**Don't over-interpret.** If Assist says it worked, it worked. Trust the response.

## When Assist Returns an Error

Only if Assist returns an error (`response_type: "error"`), you can **suggest HA-side improvements**:

| Error | Suggestion |
|-------|------------|
| `no_intent_match` | "HA didn't recognize that command" |
| `no_valid_targets` | "Try checking the entity name in HA, or add an alias" |
| Multiple devices | "There may be duplicate names — consider adding unique aliases in HA" |

These are **suggestions for improving HA config**, not skill failures.

## API Reference

Endpoint: `POST /api/conversation/process`

**Note:** Use `/api/conversation/process`, NOT `/api/services/conversation/process`.

Request:

```json
{
  "text": "turn on the kitchen lights",
  "language": "en"
}
```

Response:

```json
{
  "response": {
    "speech": {
      "plain": {"speech": "Turned on the light"}
    },
    "response_type": "action_done",
    "data": {
      "success": [{"name": "Kitchen Light", "id": "light.kitchen"}],
      "failed": []
    }
  }
}
```

## Security Notes

- Never print, echo, log, or store the Home Assistant token.
- Do not place the token in shell commands if an in-memory tool is available.
- Prefer `homeassistant-assist-token` and `homeassistant-entity-search-token` in Autobot.
- If a wrapper reports a missing credential, validate with `get_credential("homeassistant")` and use the token-based tool variant as a fallback.

## Philosophy

- **Trust Assist** — It knows the user's HA setup better than we do
- **Fire and forget** — Pass the request, relay the response
- **Don't troubleshoot** — If something doesn't work, suggest HA config improvements
- **Keep it simple** — One API call, natural language in, natural language out

## Links

- [Home Assistant Conversation API Docs](https://developers.home-assistant.io/docs/intent_conversation_api/)
