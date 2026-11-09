"""Additive list metadata; the existing items envelope and item fields are retained."""


def paginate(items: list[dict], *, page: int, page_size: int, q: str = "", status: str = "") -> dict:
    query = q.casefold().strip()
    filtered = [item for item in items if (not query or query in (item.get("name", "") + " " + item.get("description", "")).casefold())
                and (not status or item.get("status") == status)]
    filtered.sort(key=lambda item: (item.get("updated_at", item.get("created_at", "")), item["id"]), reverse=True)
    offset = (page - 1) * page_size
    return {"items": filtered[offset:offset + page_size], "total": len(filtered), "page": page, "page_size": page_size}
