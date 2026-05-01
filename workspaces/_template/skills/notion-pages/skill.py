import requests

NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"
TIMEOUT = 30


def _get_token(_agent):
    cred = _agent.get_credential("notion")
    if not cred or cred.get("type") != "token" or not cred.get("value"):
        raise ValueError("Credential `notion` (token) not found")
    return cred["value"]


def _headers(_agent):
    token = _get_token(_agent)
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(_agent, method, path, payload=None):
    headers = _headers(_agent)
    url = f"{BASE_URL}{path}"
    kwargs = {"headers": headers, "timeout": TIMEOUT}
    if payload is not None:
        kwargs["json"] = payload
    response = requests.request(method, url, **kwargs)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}

    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "error": data.get("message") or response.text,
            "notion_error": data,
        }
    return {"ok": True, "status": response.status_code, "data": data}


def verify_api(_agent=None):
    result = _request(_agent, "POST", "/search", {"page_size": 1})
    if not result["ok"]:
        return result
    data = result["data"]
    return {
        "ok": True,
        "status": result.get("status", 200),
        "message": "Notion API reachable and credential accepted",
        "result_count": len(data.get("results", []) or []),
        "has_more": data.get("has_more", False),
    }


def _extract_title(page_obj):
    props = page_obj.get("properties", {}) or {}
    for prop in props.values():
        if prop.get("type") == "title":
            items = prop.get("title", []) or []
            text = "".join((i.get("plain_text") or "") for i in items).strip()
            if text:
                return text
    return "(sin título)"


def _normalize_page(obj):
    return {
        "id": obj.get("id"),
        "title": _extract_title(obj),
        "url": obj.get("url"),
        "last_edited_time": obj.get("last_edited_time"),
    }


def list_pages(_agent=None, query=None, page_size=50):
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 50
    page_size = max(1, min(page_size, 100))

    payload = {"page_size": page_size}
    if query:
        payload["query"] = query

    result = _request(_agent, "POST", "/search", payload)
    if not result["ok"]:
        return result

    data = result["data"]
    pages = []
    for obj in data.get("results", []):
        if obj.get("object") == "page":
            pages.append(_normalize_page(obj))

    return {
        "ok": True,
        "count": len(pages),
        "results": pages,
        "has_more": data.get("has_more", False),
        "next_cursor": data.get("next_cursor"),
    }


def create_page(_agent=None, database_id=None, title=None, content=None):
    if not database_id:
        raise ValueError("`database_id` es requerido para create_page")
    if not title:
        raise ValueError("`title` es requerido para create_page")

    children = []
    if content:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
            },
        })

    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
    }
    if children:
        payload["children"] = children

    result = _request(_agent, "POST", "/pages", payload)
    if not result["ok"]:
        return result

    obj = result["data"]
    return {
        "ok": True,
        "id": obj.get("id"),
        "url": obj.get("url"),
        "created_time": obj.get("created_time"),
    }


def create_subpage(_agent=None, parent_page_id=None, title=None, content=None):
    if not parent_page_id:
        raise ValueError("`parent_page_id` es requerido para create_subpage")
    if not title:
        raise ValueError("`title` es requerido para create_subpage")

    children = []
    if content:
        for paragraph in [p.strip() for p in content.split("\n\n") if p.strip()]:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": paragraph[:2000]}}]
                },
            })

    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
    }
    if children:
        payload["children"] = children

    result = _request(_agent, "POST", "/pages", payload)
    if not result["ok"]:
        return result

    obj = result["data"]
    return {
        "ok": True,
        "id": obj.get("id"),
        "url": obj.get("url"),
        "created_time": obj.get("created_time"),
        "title": title,
    }


def handler(_agent=None, action=None, query=None, database_id=None, parent_page_id=None, title=None, content=None, page_size=50, **kwargs):
    if action == "verify_api":
        return verify_api(_agent=_agent)
    if action == "list_pages":
        return list_pages(_agent=_agent, query=query, page_size=page_size)
    if action == "create_page":
        return create_page(_agent=_agent, database_id=database_id, title=title, content=content)
    if action == "create_subpage":
        return create_subpage(_agent=_agent, parent_page_id=parent_page_id, title=title, content=content)
    return {
        "ok": False,
        "error": "`action` debe ser `verify_api`, `list_pages`, `create_page` o `create_subpage`",
    }
