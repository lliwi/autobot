"""Outbound HTTP fetch tool."""
from app.runtime.tool_registry.core import ToolDefinition, register

_FETCH_MAX_BYTES = 200_000


def register_web_tools():
    register(
        ToolDefinition(
            name="fetch_url",
            description=(
                "Fetch the contents of an HTTP(S) URL. Returns up to 200 KB of text. "
                "Use to read web pages, JSON APIs, or RSS feeds when building or running a skill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to fetch (http or https)."},
                    "method": {"type": "string", "description": "HTTP method (default GET)."},
                    "headers": {
                        "type": "object",
                        "description": "Optional HTTP headers as a flat string-to-string map.",
                    },
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _fetch_url(**kwargs),
        )
    )


def _fetch_url(_agent=None, url=None, method="GET", headers=None, **kwargs):
    if not url:
        return {"error": "Missing required argument 'url'"}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "URL must start with http:// or https://"}
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            resp = client.request(method.upper(), url, headers=headers or None)
    except httpx.HTTPError as e:
        return {"error": f"Request failed: {e}"}

    body = resp.text
    truncated = False
    if len(body.encode("utf-8", errors="replace")) > _FETCH_MAX_BYTES:
        body = body[:_FETCH_MAX_BYTES]
        truncated = True

    return {
        "url": str(resp.url),
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type"),
        "body": body,
        "truncated": truncated,
    }
