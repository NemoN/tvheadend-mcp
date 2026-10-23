"""EPG configuration, native grabber control, source mapping and coverage reports."""

import asyncio
import time
from typing import Any, Literal

from .client import TvheadendError
from .operations import inventory, rows
from .schema import uuid_hex, validate_fields, values


def coverage(events: list[dict[str, Any]], start: int, stop: int) -> dict[str, Any]:
    intervals = sorted(
        (max(start, item["start"]), min(stop, item["stop"]))
        for item in events
        if type(item.get("start")) is int
        and type(item.get("stop")) is int
        and item["start"] < item["stop"]
        and item["start"] < stop
        and item["stop"] > start
    )
    covered = 0
    cursor = start
    gaps = []
    for left, right in intervals:
        if left > cursor:
            gaps.append({"start": cursor, "stop": left, "seconds": left - cursor})
        covered += max(0, right - max(cursor, left))
        cursor = max(cursor, right)
    if cursor < stop:
        gaps.append({"start": cursor, "stop": stop, "seconds": stop - cursor})
    valid = [
        e
        for e in events
        if type(e.get("start")) is int
        and type(e.get("stop")) is int
        and e["start"] < e["stop"]
        and e["start"] < stop
        and e["stop"] > start
    ]
    described = sum(bool(e.get("description") or e.get("summary")) for e in valid)
    return {
        "events": len(valid),
        "described_events": described,
        "coverage_percent": round(100 * covered / (stop - start), 2),
        "covered_seconds": covered,
        "gap_count": len(gaps),
        "largest_gap_seconds": max((g["seconds"] for g in gaps), default=0),
        "gaps": gaps[:50],
        "gaps_truncated": len(gaps) > 50,
    }


class EpgTools:
    async def get_epg_config(self) -> dict[str, Any]:
        """Read global EPG grabber settings and their live metadata, including schedules."""
        nodes = rows(await self.client.request("epggrab/config/load", {"meta": 1}))
        if len(nodes) != 1:
            raise TvheadendError("Expected one global EPG configuration object")
        return nodes[0]

    async def update_epg_config(
        self, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Update global EPG fields using epggrab/config/save and the running build's schema.

        Read get_epg_config first. Use the actual cron/OTA/save fields it advertises.
        This does not install grabbers or upload XMLTV data.
        """
        async with self._write_lock:
            node = await self.get_epg_config()
            validate_fields(node, settings)
            result = await self._write("epggrab/config/save", {"node": settings}, dry_run)
            result["before"] = {k: values(node).get(k) for k in settings}
            if not dry_run:
                try:
                    result["after"] = await self.get_epg_config()
                except TvheadendError as exc:
                    result["readback_error"] = str(exc)
            return result

    async def list_epg_modules(self) -> dict[str, Any]:
        """List native OTA/internal/external grabber modules, UUIDs and enabled status."""
        return {"entries": rows(await self.client.request("epggrab/module/list"))}

    async def update_epg_module(
        self, module_uuid: str, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Configure an installed grabber module, e.g. enabled/priority, using its live fields."""
        async with self._write_lock:
            module_uuid = uuid_hex(module_uuid)
            modules = rows(await self.client.request("epggrab/module/list"))
            if module_uuid not in {m.get("uuid") for m in modules}:
                raise ValueError("Select an installed UUID from list_epg_modules")
            return await self._update(await self._node(module_uuid), settings, dry_run)

    async def list_epg_sources(self, start: int = 0, limit: int = 100) -> dict[str, Any]:
        """List EPG source channels, module IDs and existing Tvheadend channel mappings."""
        data = await self.client.grid("epggrab/channel/grid", start=start, limit=limit)
        return data if isinstance(data, dict) else {"entries": data}

    async def assign_epg_sources(
        self,
        channel_uuid: str,
        source_ids: list[str],
        mode: Literal["add", "replace", "remove"] = "add",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Assign advertised EPG source IDs to a channel; add preserves existing sources.

        Source IDs are enum keys from get_field_options(channel_uuid, 'epggrab'),
        often module/channel strings rather than UUIDs. Empty replace explicitly clears.
        """
        async with self._write_lock:
            node = await self._node(channel_uuid)
            if node.get("class") != "channel":
                raise ValueError("Select a channel UUID")
            options = await self.get_field_options(channel_uuid, "epggrab")
            advertised = options.get("options", options.get("entries", []))
            if isinstance(advertised, dict):
                advertised = advertised.get("entries", [])
            keys = {item.get("key") for item in advertised if isinstance(item, dict)}
            current = values(node).get("epggrab", [])
            if not isinstance(current, list) or not all(isinstance(s, str) for s in source_ids):
                raise ValueError("EPG sources must be a list of advertised string IDs")
            if set(source_ids) - keys - (set(current) if mode == "remove" else set()):
                raise ValueError("Unknown EPG source ID; inspect the channel's field options")
            if mode == "add":
                assigned = list(dict.fromkeys([*current, *source_ids]))
            elif mode == "replace":
                assigned = list(dict.fromkeys(source_ids))
            elif mode == "remove":
                assigned = [key for key in current if key not in source_ids]
            else:
                raise ValueError("mode must be add, replace or remove")
            return await self._update(node, {"epggrab": assigned}, dry_run)

    async def trigger_epg_grab(
        self,
        kind: Literal["ota", "internal"] = "ota",
        delay_seconds: int = 1,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Queue OTA collection or rerun enabled internal grabbers; collection is asynchronous.

        OTA delay: 1..604800 seconds. Internal grabbers run immediately (delay must be 1).
        External XMLTV socket imports are not triggered by this endpoint.
        """
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 604800:
            raise ValueError("delay_seconds must be between 1 and 604800")
        if kind == "ota":
            return await self._write("epggrab/ota/trigger", {"trigger": delay_seconds}, dry_run)
        if kind == "internal" and delay_seconds == 1:
            return await self._write("epggrab/internal/rerun", {"rerun": 1}, dry_run)
        raise ValueError("Select ota or internal; internal does not accept a delay")

    async def epg_coverage(
        self, channel_uuids: list[str] | None = None, hours: int = 24
    ) -> dict[str, Any]:
        """Measure time coverage, gaps and description presence for the next 1..168 hours.

        Defaults to all enabled channels (maximum 500); explicit selection includes disabled
        channels. Overlapping events are counted once. Incomplete reads report errors rather
        than claiming full coverage. Does not tune a receiver or prove reception quality.
        """
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("hours must be between 1 and 168")
        channels = await inventory(self.client, "channel/grid", {"all": 1})
        selected = None if channel_uuids is None else {uuid_hex(u) for u in channel_uuids}
        known = {c["uuid"] for c in channels}
        if selected is not None and selected - known:
            raise ValueError("Unknown or inaccessible channel UUID")
        channels = [
            c
            for c in channels
            if (c["uuid"] in selected if selected is not None else c.get("enabled", True))
        ]
        if len(channels) > 500:
            raise ValueError("Select at most 500 channels per coverage report")
        start = int(time.time())
        stop = start + hours * 3600
        gate = asyncio.Semaphore(4)

        async def measure(channel: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                item = {"uuid": channel["uuid"], "name": channel.get("name", "")}
                try:
                    events = await inventory(
                        self.client,
                        "epg/events/grid",
                        {
                            "channel": channel["uuid"],
                            "sort": "start",
                            "dir": "ASC",
                            "filter": [
                                {
                                    "field": "stop",
                                    "type": "numeric",
                                    "comparison": "gt",
                                    "value": start,
                                },
                                {
                                    "field": "start",
                                    "type": "numeric",
                                    "comparison": "lt",
                                    "value": stop,
                                },
                            ],
                        },
                        maximum=10000,
                    )
                    return {**item, **coverage(events, start, stop), "complete": True}
                except TvheadendError as exc:
                    return {**item, "complete": False, "error": str(exc)}

        report = await asyncio.gather(*(measure(c) for c in channels))
        return {
            "start": start,
            "stop": stop,
            "hours": hours,
            "channels": report,
            "complete": all(item["complete"] for item in report),
            "channels_without_events": [i["uuid"] for i in report if i.get("events") == 0],
        }
