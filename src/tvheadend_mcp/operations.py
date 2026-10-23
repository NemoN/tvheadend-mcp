"""Bounded inventory reads shared by diagnostics and planned maintenance."""

from typing import Any

from .client import TvheadendClient, TvheadendError


def rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, (dict, list)) or isinstance(data, dict) and "entries" not in data:
        raise TvheadendError("Tvheadend returned no inventory entries field")
    items = data if isinstance(data, list) else data["entries"]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise TvheadendError("Tvheadend returned an invalid inventory")
    return items


async def inventory(
    client: TvheadendClient,
    path: str,
    extra: dict[str, Any] | None = None,
    maximum: int = 50000,
) -> list[dict[str, Any]]:
    """Read every page or fail explicitly; never plan changes from a truncated grid."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    while len(result) < maximum:
        limit = min(1000, maximum - len(result))
        params = dict(extra or {})
        filters = params.pop("filter", None)
        data = await client.grid(
            path, start=len(result), limit=limit, extra=params, filters=filters
        )
        page = rows(data)
        if len(page) > limit:
            raise TvheadendError("Tvheadend ignored the requested inventory page limit")
        for item in page:
            key = str(item.get("uuid", item.get("eventId", "")))
            if key and key in seen:
                raise TvheadendError("Inventory changed or pagination repeated; retry the read")
            if key:
                seen.add(key)
        total = data.get("total", data.get("totalCount")) if isinstance(data, dict) else None
        result.extend(page)
        if total is not None:
            if type(total) is not int or total < 0:
                raise TvheadendError("Invalid inventory total")
            if len(result) >= total:
                return result
            if not page:
                raise TvheadendError("Inventory ended before its reported total")
        elif len(page) < limit:
            return result
    raise TvheadendError(
        f"Inventory exceeds the {maximum}-entry safety limit; narrow the selection"
    )


def network_fields(settings: dict[str, Any]) -> dict[str, list[str]]:
    return {
        key: value
        for key, value in settings.items()
        if (key == "networks" or key.startswith("network_")) and isinstance(value, list)
    }


def active_inputs(data: Any) -> list[dict[str, Any]]:
    """Some devices report idle status rows; missing activity fields are inconclusive."""
    return [
        row
        for row in rows(data)
        if (
            row.get("subs", 0)
            or row.get("bps", 0)
            or row.get("stream")
            or not {"subs", "bps", "stream"}.intersection(row)
        )
    ]
