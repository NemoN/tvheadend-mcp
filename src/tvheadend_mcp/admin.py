"""Domain tools. UUIDs, available fields and enum values always come from Tvheadend."""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .client import JSON, TvheadendClient, TvheadendError
from .schema import properties, uuid_hex, validate_fields, values


def entries(data: JSON) -> list[dict[str, Any]]:
    result = data if isinstance(data, list) else data.get("entries", [])
    return [item for item in result if isinstance(item, dict)]


def as_dict(data: JSON) -> dict[str, Any]:
    return data if isinstance(data, dict) else {"entries": data}


class TvheadendAdmin:
    def __init__(self, client: TvheadendClient):
        self.client = client
        # Serialize read/modify/write sequences made through this MCP process.
        self._write_lock = asyncio.Lock()

    async def _node(self, uuid: str) -> dict[str, Any]:
        uuid = uuid_hex(uuid)
        nodes = entries(await self.client.request("idnode/load", {"uuid": uuid, "meta": 1}))
        for node in nodes:
            if node.get("uuid", node.get("id")) == uuid:
                return node
        raise TvheadendError(f"Node {uuid} was not found or is inaccessible")

    async def _write(self, path: str, params: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "endpoint": path, "parameters": params}
        response = await self.client.request(path, params, write=True)
        return {"dry_run": False, "endpoint": path, "response": response}

    async def _update(
        self, node: dict[str, Any], settings: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        validate_fields(node, settings)
        uuid = uuid_hex(node["uuid"])
        result = await self._write("idnode/save", {"node": {"uuid": uuid, **settings}}, dry_run)
        old = values(node)
        result["before"] = {key: old.get(key) for key in settings}
        if not dry_run:
            # A satconf or switch-type update can replace child UUIDs. Re-read the parent.
            try:
                result["after"] = await self._node(uuid)
            except TvheadendError as exc:
                # The save already succeeded. Do not present a readback failure as a
                # failed write, which could encourage repeating a topology change.
                result["readback_error"] = str(exc)
        return result

    async def _create(
        self,
        path: str,
        schema: dict[str, Any],
        settings: dict[str, Any],
        dry_run: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_fields(schema, settings, creating=True)
        result = await self._write(path, {**(extra or {}), "conf": settings}, dry_run)
        if not dry_run:
            response = result["response"]
            if not isinstance(response, dict) or not response.get("uuid"):
                raise TvheadendError(
                    f"{path} returned no new UUID. Inspect the server before retrying creation."
                )
        return result

    async def status(self) -> dict[str, Any]:
        """Read server version, active tuners/signals and subscriptions, with permission errors."""
        paths = ["serverinfo", "status/inputs", "status/subscriptions"]
        results = await asyncio.gather(
            *(self.client.request(path) for path in paths), return_exceptions=True
        )
        return {
            "read_only": self.client.settings.read_only,
            "timezone": self.client.settings.timezone,
            "results": {
                path: {"error": str(value)} if isinstance(value, Exception) else value
                for path, value in zip(paths, results, strict=True)
            },
        }

    async def hardware_tree(
        self, uuid: str = "root", recursive: bool = False, max_depth: int = 8
    ) -> dict[str, Any]:
        """List adapters, frontends, satellite inputs, LNBs, switches, rotors and Unicable nodes.

        uuid='root' lists adapters; a device UUID lists its children. recursive=true
        expands children, with their actual UUIDs, settings, descriptions and option metadata.
        Physical tuners are discovered by Tvheadend/its drivers, not created by this tool.
        """
        if uuid != "root":
            uuid = uuid_hex(uuid)
        if not 1 <= max_depth <= 12:
            raise ValueError("max_depth must be between 1 and 12")
        visited: set[str] = set()

        async def walk(parent: str, depth: int) -> list[dict[str, Any]]:
            nodes = entries(await self.client.request("hardware/tree", {"uuid": parent}))
            for node in nodes:
                key = node.get("uuid", node.get("id"))
                if recursive and not node.get("leaf") and key and key not in visited:
                    visited.add(key)
                    if depth < max_depth:
                        node["children"] = await walk(key, depth + 1)
                    else:
                        node["children_truncated"] = True
            return nodes

        return {"entries": await walk(uuid, 1)}

    async def discover_satip(self, dry_run: bool = False) -> dict[str, Any]:
        """Trigger Tvheadend's SAT>IP server discovery. Requires SAT>IP client support."""
        return await self._write("hardware/satip/discover", {"op": "all"}, dry_run)

    async def list_classes(self, contains: str = "") -> dict[str, Any]:
        """Discover available Tvheadend class names, optionally filtering name/caption."""
        data = await self.client.request("classes")
        if contains and isinstance(data, dict):
            data = {
                key: val
                for key, val in data.items()
                if contains.casefold() in (key + " " + str(val)).casefold()
            }
        return as_dict(data)

    async def get_schema(self, class_name: str) -> dict[str, Any]:
        """Get fields/types/defaults/options for a class found via list_classes or hardware_tree."""
        return as_dict(await self.client.request("idnode/class", {"name": class_name}))

    async def get_node(self, uuid: str) -> dict[str, Any]:
        """Inspect one object's current values and metadata, including advanced/expert fields."""
        return await self._node(uuid)

    async def get_field_options(self, uuid: str, field: str) -> dict[str, Any]:
        """Resolve a field's static or API-backed enum: networks, LNB types, switches, etc."""
        node = await self._node(uuid)
        prop = properties(node).get(field)
        if prop is None:
            raise ValueError(f"Unknown field {field!r}")
        enum = prop.get("enum")
        if isinstance(enum, dict) and enum.get("type") == "api":
            path = enum.get("uri", "").removeprefix("api/")
            return await self.api_read(path, enum.get("params", {}))
        return {"field": field, "options": enum, "metadata": prop}

    async def update_node(
        self, uuid: str, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Update any API-exposed tuner, LNB, DiSEqC, Unicable, network, mux, service,
        channel, EPG, bouquet or DVR object. Inspect get_node/get_field_options first.

        Uses actual field IDs and native units, validates unknown/read-only fields,
        preserves unspecified fields; arrays REPLACE existing arrays. dry_run previews.
        After satconf/lnb_type/switch_type/en50494_type changes, re-read hardware_tree:
        child nodes may have been recreated. No hard-coded limits on expert settings.
        """
        async with self._write_lock:
            return await self._update(await self._node(uuid), settings, dry_run)

    async def assign_networks(
        self,
        hardware_uuid: str,
        network_uuids: list[str],
        field: str = "networks",
        mode: Literal["add", "replace", "remove"] = "add",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Assign networks to the exact frontend or satellite-input node that owns the field.

        For DVB-C/T use frontend.networks. For DVB-S inspect the hardware tree:
        networks or network_a/network_aa etc. may live on satellite configuration/input
        nodes. Use field options to match supported network types. 'add' preserves existing
        assignments, 'replace' replaces them, 'remove' removes only the listed networks.
        """
        ids = [uuid_hex(u) for u in network_uuids]
        async with self._write_lock:
            node = await self._node(hardware_uuid)
            prop = properties(node).get(field, {})
            if not (field == "networks" or field.startswith("network_")) or not prop.get("list"):
                raise ValueError(
                    "Select an actual network-assignment array from this node's metadata"
                )
            for key in ids:
                network = await self._node(key)
                if "network" not in network.get("class", ""):
                    raise ValueError(f"{key} is not a DVB/IPTV network")
            current = values(node).get(field, [])
            if mode == "add":
                assigned = list(dict.fromkeys([*current, *ids]))
            elif mode == "replace":
                assigned = list(dict.fromkeys(ids))
            elif mode == "remove":
                assigned = [u for u in current if u not in ids]
            else:
                raise ValueError("mode must be add, replace or remove")
            return await self._update(node, {field: assigned}, dry_run)

    async def list_networks(
        self, start: int = 0, limit: int = 100, filters: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """List networks with UUID, delivery system, enabled state, scan queue and counts."""
        return as_dict(
            await self.client.grid("mpegts/network/grid", start=start, limit=limit, filters=filters)
        )

    async def network_types(self) -> dict[str, Any]:
        """List creatable DVB-S/C/T, ATSC, ISDB and IPTV network classes and their schemas."""
        return as_dict(await self.client.request("mpegts/network/builders"))

    async def create_network(
        self, class_name: str, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Create a network using a class/schema from network_types.

        Pass networkname, enabled and delivery-specific fields such as orbital_pos or
        scanfile as supported by this build. Existing networks are never silently reused.
        """
        types = entries(await self.client.request("mpegts/network/builders"))
        schema = next((item for item in types if item.get("class") == class_name), None)
        if schema is None:
            raise ValueError("Unknown network class; use network_types first")
        return await self._create(
            "mpegts/network/create", schema, settings, dry_run, {"class": class_name}
        )

    async def list_muxes(
        self,
        network_uuid: str | None = None,
        start: int = 0,
        limit: int = 100,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """List muxes including scan state/result; optionally filter by network UUID."""
        selected = list(filters or [])
        if network_uuid:
            selected.append(
                {
                    "field": "network_uuid",
                    "type": "string",
                    "value": "^" + uuid_hex(network_uuid) + "$",
                }
            )
        return as_dict(
            await self.client.grid(
                "mpegts/mux/grid",
                start=start,
                limit=limit,
                filters=selected,
                extra={"hidemode": "none"},
            )
        )

    async def mux_schema(self, network_uuid: str) -> dict[str, Any]:
        """Read network-specific mux fields, tuning parameters and modulation/FEC enums."""
        return as_dict(
            await self.client.request("mpegts/network/mux_class", {"uuid": uuid_hex(network_uuid)})
        )

    async def create_mux(
        self,
        network_uuid: str,
        settings: dict[str, Any],
        frequency_hz: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a mux with network-specific tuning fields (symbolrate, FEC, PLP/PLS, etc.).

        frequency_hz is always Hz: converted to native kHz for DVB-S/S2, Hz for DVB-C/T.
        Alternatively pass settings.frequency in Tvheadend's NATIVE units. Do not pass
        both. symbolrate is symbols/second, not ksymbols/second. Inspect mux_schema first.
        """
        network_uuid = uuid_hex(network_uuid)
        schema = await self.mux_schema(network_uuid)
        conf = dict(settings)
        if frequency_hz is not None:
            if "frequency" in conf or type(frequency_hz) is not int or frequency_hz <= 0:
                raise ValueError("Use a positive integer frequency_hz OR settings.frequency")
            satellite = "dvbs" in schema.get("class", "").lower()
            if satellite and frequency_hz % 1000:
                raise ValueError("DVB-S frequencies must be an exact multiple of 1000 Hz")
            conf["frequency"] = frequency_hz // 1000 if satellite else frequency_hz
        return await self._create(
            "mpegts/network/mux_create", schema, conf, dry_run, {"uuid": network_uuid}
        )

    async def scan_networks(
        self, network_uuids: list[str], dry_run: bool = False
    ) -> dict[str, Any]:
        """Queue a service scan for selected networks; returns immediately.

        Monitor list_networks.scanq_length and list_muxes.scan_state/scan_result.
        Scans use tuner capacity. For one mux, update_node with its schema's scan_state.
        """
        if not network_uuids:
            raise ValueError("Select at least one network")
        return await self._write(
            "mpegts/network/scan", {"uuid": [uuid_hex(u) for u in network_uuids]}, dry_run
        )

    async def list_services(
        self,
        mux_uuid: str | None = None,
        start: int = 0,
        limit: int = 100,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """List discovered services including disabled/unverified ones, encryption and mappings."""
        selected = list(filters or [])
        if mux_uuid:
            selected.append(
                {
                    "field": "multiplex_uuid",
                    "type": "string",
                    "value": "^" + uuid_hex(mux_uuid) + "$",
                }
            )
        return as_dict(
            await self.client.grid(
                "mpegts/service/grid",
                start=start,
                limit=limit,
                filters=selected,
                extra={"hidemode": "none"},
            )
        )

    async def map_services(
        self, service_uuids: list[str], options: dict[str, Any] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Start Tvheadend's native service-to-channel mapper for explicit service UUIDs.

        Optional fields: encrypted, merge_same_name, check_availability, type_tags, etc.
        Uses service/mapper/save, which starts the mapper; there is no mapper/start API.
        Empty selection is rejected. Monitor mapper_status and list_channels afterwards.
        """
        if not service_uuids:
            raise ValueError("Select at least one service UUID")
        conf = dict(options or {})
        if "services" in conf:
            raise ValueError("Supply the selection through service_uuids, not options.services")
        conf["services"] = [uuid_hex(u) for u in service_uuids]
        async with self._write_lock:
            schema = entries(await self.client.request("service/mapper/load", {"meta": 1}))
            if not schema:
                raise TvheadendError("The server returned no mapper configuration")
            validate_fields(schema[0], conf)
            return await self._write("service/mapper/save", {"node": conf}, dry_run)

    async def mapper_status(self) -> dict[str, Any]:
        """Read service mapper success/failure counters and mapper configuration/options."""
        return {
            "status": await self.client.request("service/mapper/status"),
            "configuration": await self.client.request("service/mapper/load", {"meta": 1}),
        }

    async def stop_mapper(self, dry_run: bool = False) -> dict[str, Any]:
        """Stop a running automatic service-to-channel mapping operation."""
        return await self._write("service/mapper/stop", {}, dry_run)

    async def list_channels(
        self, start: int = 0, limit: int = 100, filters: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """List all accessible channels, including disabled ones, with service and EPG mappings."""
        return as_dict(
            await self.client.grid(
                "channel/grid", start=start, limit=limit, filters=filters, extra={"all": 1}
            )
        )

    async def create_channel(
        self, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Create a channel: name, number, services UUID array, tags, EPG settings, etc."""
        schema = as_dict(await self.client.request("channel/class"))
        return await self._create("channel/create", schema, settings, dry_run)

    async def assign_services(
        self,
        channel_uuid: str,
        service_uuids: list[str],
        mode: Literal["add", "replace", "remove"] = "add",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add, replace or remove a channel's service mappings, preserving others in add mode."""
        ids = [uuid_hex(u) for u in service_uuids]
        async with self._write_lock:
            node = await self._node(channel_uuid)
            if node.get("class") != "channel":
                raise ValueError("channel_uuid must identify a channel")
            for key in ids:
                service = await self._node(key)
                if "service" not in service.get("class", ""):
                    raise ValueError(f"{key} is not a service")
            current = values(node).get("services", [])
            if mode == "add":
                assigned = list(dict.fromkeys([*current, *ids]))
            elif mode == "replace":
                assigned = list(dict.fromkeys(ids))
            elif mode == "remove":
                assigned = [u for u in current if u not in ids]
            else:
                raise ValueError("mode must be add, replace or remove")
            return await self._update(node, {"services": assigned}, dry_run)

    async def list_channel_tags(self, start: int = 0, limit: int = 100) -> dict[str, Any]:
        """List channel groups/tags. Edit membership through channel.tags using update_node."""
        return as_dict(
            await self.client.grid("channeltag/grid", start=start, limit=limit, extra={"all": 1})
        )

    async def create_channel_tag(
        self, settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Create a channel tag/group using fields from the channeltag class schema."""
        schema = as_dict(await self.client.request("channeltag/class"))
        return await self._create("channeltag/create", schema, settings, dry_run)

    async def search_epg(
        self,
        query: str = "",
        channel_uuid: str | None = None,
        fulltext: bool = True,
        start: int = 0,
        limit: int = 50,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Search upcoming EPG programmes; query is a Tvheadend regular expression.

        Results contain eventId for schedule_recording. Optional EPG-specific grid filters
        support time bounds; timestamps are Unix seconds. fulltext includes descriptions.
        """
        return as_dict(
            await self.client.grid(
                "epg/events/grid",
                start=start,
                limit=limit,
                filters=filters,
                extra={
                    "title": query,
                    "channel": uuid_hex(channel_uuid) if channel_uuid else None,
                    "fulltext": fulltext,
                    "sort": "start",
                    "dir": "ASC",
                },
            )
        )

    async def list_recordings(
        self,
        state: Literal["all", "upcoming", "finished", "failed", "removed"] = "all",
        start: int = 0,
        limit: int = 100,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """List DVR entries: upcoming includes active recordings; paginate using total/start."""
        if state not in {"all", "upcoming", "finished", "failed", "removed"}:
            raise ValueError("Invalid recording state")
        suffix = "" if state == "all" else "_" + state
        return as_dict(
            await self.client.grid(
                "dvr/entry/grid" + suffix, start=start, limit=limit, filters=filters
            )
        )

    async def schedule_recording(
        self, event_id: int, config_uuid: str = "", comment: str = "", dry_run: bool = False
    ) -> dict[str, Any]:
        """Record an EPG eventId; config_uuid='' selects Tvheadend's default DVR profile."""
        if event_id <= 0:
            raise ValueError("event_id must be positive")
        if config_uuid:
            config_uuid = uuid_hex(config_uuid)
        result = await self._write(
            "dvr/entry/create_by_event",
            {
                "entries": [
                    {"event_id": str(event_id), "config_uuid": config_uuid, "comment": comment}
                ]
            },
            dry_run,
        )
        if not dry_run and (
            not isinstance(result["response"], dict) or not result["response"].get("uuid")
        ):
            raise TvheadendError(
                "No recording created; the event may be missing or already scheduled."
            )
        return result

    def _timestamp(self, text: str) -> int:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Use ISO 8601 with timezone, e.g. 2026-10-25T20:15:00+01:00") from None
        if dt.tzinfo is None:
            raise ValueError("Include an explicit UTC offset to avoid DST ambiguity")
        return int(dt.timestamp())

    async def create_timer(
        self,
        channel_uuid: str,
        title: str,
        start_time: str,
        stop_time: str,
        config_uuid: str = "",
        extra: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a manual DVR timer. start_time/stop_time: ISO 8601 with explicit UTC offset.

        extra accepts recording fields such as start_extra/stop_extra (minutes), pri,
        comment. Core channel/title/time/profile fields cannot be overridden via extra.
        """
        start, stop = self._timestamp(start_time), self._timestamp(stop_time)
        if stop <= start:
            raise ValueError("stop_time must be after start_time")
        core = {
            "channel": uuid_hex(channel_uuid),
            "disp_title": title,
            "start": start,
            "stop": stop,
            "config_name": uuid_hex(config_uuid) if config_uuid else "",
        }
        if not title.strip():
            raise ValueError("title must not be empty")
        if set(extra or {}) & core.keys():
            raise ValueError("extra cannot override the channel/title/times/profile arguments")
        schema = as_dict(await self.client.request("dvr/entry/class"))
        return await self._create("dvr/entry/create", schema, {**core, **(extra or {})}, dry_run)

    async def recording_action(
        self, uuid: str, action: Literal["stop", "cancel", "remove_files"], dry_run: bool = False
    ) -> dict[str, Any]:
        """stop finishes an active recording; cancel cancels a timer/active recording.

        remove_files PERMANENTLY DELETES the recording files through Tvheadend's remove
        endpoint. Use only when the user explicitly requests deletion of recordings.
        """
        actions = {"stop": "stop", "cancel": "cancel", "remove_files": "remove"}
        if action not in actions:
            raise ValueError("action must be stop, cancel or remove_files")
        return await self._write("dvr/entry/" + actions[action], {"uuid": uuid_hex(uuid)}, dry_run)

    async def list_dvr_rules(
        self, kind: Literal["autorec", "timerec"] = "autorec", start: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """List automatic EPG recording rules (autorec) or recurring clock timers (timerec)."""
        if kind not in {"autorec", "timerec"}:
            raise ValueError("kind must be autorec or timerec")
        return as_dict(await self.client.grid(f"dvr/{kind}/grid", start=start, limit=limit))

    async def create_dvr_rule(
        self, kind: Literal["autorec", "timerec"], settings: dict[str, Any], dry_run: bool = False
    ) -> dict[str, Any]:
        """Create an automatic series/title rule or recurring timer. Inspect the corresponding
        dvr_autorec/dvr_timerec class schema first; rule times use Tvheadend's native units.
        Edit/disable rules with update_node, delete them with delete_node.
        """
        if kind not in {"autorec", "timerec"}:
            raise ValueError("kind must be autorec or timerec")
        schema = as_dict(await self.client.request(f"dvr/{kind}/class"))
        return await self._create(f"dvr/{kind}/create", schema, settings, dry_run)

    async def list_dvr_profiles(self, start: int = 0, limit: int = 100) -> dict[str, Any]:
        """List DVR profiles, output directories, padding and retention settings."""
        return as_dict(await self.client.grid("dvr/config/grid", start=start, limit=limit))

    async def create_dvr_profile(
        self, name: str, settings: dict[str, Any] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Create a named DVR profile, with storage/padding/retention fields from dvr_config."""
        if not name.strip():
            raise ValueError("Profile name must not be empty")
        schema = as_dict(await self.client.request("dvr/config/class"))
        conf = {**(settings or {}), "name": name}
        return await self._create("dvr/config/create", schema, conf, dry_run, {"name": name})

    async def delete_node(self, uuid: str, dry_run: bool = False) -> dict[str, Any]:
        """Delete a DVB/channel/rule/profile object and possibly dependent objects.

        For DVR recordings use recording_action instead. Hardware availability is managed
        by the OS/Tvheadend. Network deletion can cascade to its muxes and services.
        """
        node = await self._node(uuid)
        cls = node.get("class", "")
        if cls == "dvrentry" or cls.startswith("dvr_entry"):
            raise ValueError("Use recording_action for DVR entries")
        result = await self._write("idnode/delete", {"uuid": uuid_hex(uuid)}, dry_run)
        result["object"] = {key: node.get(key) for key in ("uuid", "class", "text")}
        return result

    async def snapshot(self, uuids: list[str]) -> dict[str, Any]:
        """Export selected objects' writable, readable fields as a JSON change snapshot.

        Save the returned JSON locally. This is NOT a complete Tvheadend backup: it cannot
        recreate deleted nodes, recordings or hardware; excludes secrets and write-once fields.
        """
        if not uuids or len(uuids) > 100:
            raise ValueError("Select between 1 and 100 objects per snapshot")
        nodes = []
        for key in dict.fromkeys(uuid_hex(u) for u in uuids):
            node = await self._node(key)
            fields = {
                name: prop["value"]
                for name, prop in properties(node).items()
                if "value" in prop
                and not any(prop.get(flag) for flag in ("rdonly", "wronce", "password"))
            }
            nodes.append({"uuid": key, "class": node.get("class"), "settings": fields})
        return {
            "format": "tvheadend-mcp-snapshot-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "nodes": nodes,
        }

    async def restore_snapshot(
        self, snapshot: dict[str, Any], dry_run: bool = True
    ) -> dict[str, Any]:
        """Restore a snapshot's fields to EXISTING nodes. Default previews the changes.

        All nodes are validated before the first write. Restore is sequential, not atomic;
        partial failures report completed/remaining UUIDs. Topology changes can invalidate
        child UUIDs; prefer restoring only the fields you actually changed.
        """
        if snapshot.get("format") != "tvheadend-mcp-snapshot-v1":
            raise ValueError("Unsupported snapshot format")
        saved = snapshot.get("nodes")
        if not isinstance(saved, list) or not 1 <= len(saved) <= 100:
            raise ValueError("Snapshot must contain 1 to 100 nodes")
        async with self._write_lock:
            prepared = []
            for item in saved:
                node = await self._node(item["uuid"])
                if node.get("class") != item.get("class"):
                    raise ValueError(f"Class changed for node {item['uuid']}")
                validate_fields(node, item["settings"])
                prepared.append((node, item["settings"]))
            done = []
            for index, (node, settings) in enumerate(prepared):
                try:
                    done.append(await self._update(node, settings, dry_run))
                except (TvheadendError, ValueError) as exc:
                    return {
                        "complete": False,
                        "results": done,
                        "error": str(exc),
                        "remaining_uuids": [n["uuid"] for n, _ in prepared[index:]],
                    }
            return {"complete": True, "dry_run": dry_run, "results": done}

    async def api_read(self, path: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read additional metadata/enum/grid endpoints (e.g. bouquets, EPG grabbers,
        stream profiles, scanfiles, orbital positions). Mutating endpoints are rejected.
        API paths are relative without '/api/'; pagination can be supplied in parameters.
        """
        allowed = path in {
            "serverinfo",
            "classes",
            "status/inputs",
            "status/subscriptions",
            "status/connections",
            "service/streams",
            "raw/export",
        } or re.fullmatch(
            r"(?:mpegts|dvb|channel|channeltag|bouquet|profile|epg|epggrab|idnode|"
            r"hardware|service|dvr|codec|esfilter|caclient)/[A-Za-z0-9_/-]*"
            r"(?:list|class|grid|grid_upcoming|grid_finished|grid_failed|grid_removed|"
            r"load|tree|builders|status)",
            path,
        )
        if not allowed:
            raise ValueError("Endpoint is not in the read-only API allowlist")
        return as_dict(await self.client.request(path, parameters))

    async def diagnose_network(self, network_uuid: str) -> dict[str, Any]:
        """Inspect network assignment, scan failures and active inputs without starting a scan.

        Network/mux counts are paginated; hardware is traversed recursively. A missing
        assignment or repeated scan failures is reported as evidence, not a guessed diagnosis.
        """
        network_uuid = uuid_hex(network_uuid)
        network = await self._node(network_uuid)
        hardware = await self.hardware_tree(recursive=True)
        assignments = []

        def collect(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                for field, value in values(node).items():
                    if (field == "networks" or field.startswith("network_")) and (
                        isinstance(value, list) and network_uuid in value
                    ):
                        assignments.append(
                            {"uuid": node.get("uuid"), "text": node.get("text"), "field": field}
                        )
                collect(node.get("children", []))

        collect(hardware["entries"])
        return {
            "network": network,
            "assignments": assignments,
            "muxes": await self.list_muxes(network_uuid, limit=1000),
            "inputs": await self.client.request("status/inputs"),
            "local_time": datetime.now(ZoneInfo(self.client.settings.timezone)).isoformat(),
        }
