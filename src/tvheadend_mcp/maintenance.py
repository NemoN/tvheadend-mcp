"""Reviewed, expiring, non-deleting channel and network maintenance plans."""

import asyncio
import copy
import hashlib
import json
import re
import secrets
import time
from typing import Any

from .client import TvheadendError
from .operations import active_inputs, inventory, network_fields, rows
from .schema import properties, uuid_hex, validate_fields, values


def fingerprint(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def programme_name(name: str) -> str:
    """Remove only an explicit HD/SD suffix; retain regional words and numbers."""
    return re.sub(r"\s+(?:hd|sd)$", "", " ".join(name.casefold().split()))


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for node in nodes:
        result.append(node)
        if node.get("children_truncated"):
            raise TvheadendError("Hardware tree is truncated; maintenance cannot safely proceed")
        result.extend(flatten(node.get("children", [])))
    return result


class MaintenanceTools:
    async def _maintenance_inventory(self) -> dict[str, Any]:
        paths = {
            "channels": "channel/grid",
            "networks": "mpegts/network/grid",
            "muxes": "mpegts/mux/grid",
            "services": "mpegts/service/grid",
            "recordings": "dvr/entry/grid",
            "autorec": "dvr/autorec/grid",
            "timerec": "dvr/timerec/grid",
        }
        result = await asyncio.gather(
            *(
                inventory(self.client, path, {"all": 1, "hidemode": "none"})
                for path in paths.values()
            )
        )
        data = dict(zip(paths, result, strict=True))
        hardware = await self.hardware_tree(recursive=True, max_depth=12)
        data["hardware"] = flatten(hardware["entries"])
        return data

    async def audit_channels(self) -> dict[str, Any]:
        """Read all channels/services and report duplicate names, HD/SD candidates and problems.

        Regional names remain distinct. HD suggestions are name hints, not proof that two
        services carry the same programme. No automatic merging, numbering or deletion.
        """
        channels, services = await asyncio.gather(
            inventory(self.client, "channel/grid", {"all": 1}),
            inventory(self.client, "mpegts/service/grid", {"hidemode": "none"}),
        )
        service_ids = {s["uuid"] for s in services}
        groups: dict[str, list[dict[str, Any]]] = {}
        numbers: dict[str, list[str]] = {}
        problems = []
        for channel in channels:
            settings = values(await self._node(channel["uuid"]))
            name = str(settings.get("name") or channel.get("name") or "")
            item = {
                "uuid": channel["uuid"],
                "name": name,
                "number": settings.get("number"),
                "enabled": settings.get("enabled"),
                "hd_name_hint": name.casefold().endswith(" hd"),
            }
            groups.setdefault(programme_name(name), []).append(item)
            number = str(settings.get("number", 0))
            if number not in {"0", "0.0", "None", ""} and settings.get("enabled"):
                numbers.setdefault(number, []).append(channel["uuid"])
            mapped = settings.get("services", [])
            if not mapped or set(mapped) - service_ids:
                problems.append(
                    {
                        "uuid": channel["uuid"],
                        "no_services": not mapped,
                        "missing_services": sorted(set(mapped) - service_ids),
                    }
                )
        return {
            "channel_count": len(channels),
            "candidates": [
                {
                    "programme_name": key,
                    "channels": group,
                    "suggested_target": next((c["uuid"] for c in group if c["hd_name_hint"]), None),
                    "requires_programme_verification": True,
                }
                for key, group in sorted(groups.items())
                if key and len(group) > 1
            ],
            "duplicate_numbers": {k: v for k, v in numbers.items() if len(v) > 1},
            "mapping_problems": problems,
        }

    async def _store_plan(
        self,
        kind: str,
        recipe: dict[str, Any],
        data: dict[str, Any],
        changes: list[dict[str, Any]],
        blocked: list[Any],
        warnings: list[Any],
    ) -> dict[str, Any]:
        if len(changes) > 100:
            blocked.append(
                "Plan exceeds 100 changed nodes; split the maintenance into smaller steps"
            )
        nodes = []
        for change in changes:
            node = await self._node(change["uuid"])
            validate_fields(node, change["settings"])
            current = values(node)
            if any(key not in current for key in change["settings"]):
                raise TvheadendError(
                    "A field has no readable prior value; refusing a non-recoverable plan"
                )
            change["before"] = {k: current[k] for k in change["settings"]}
            nodes.append(
                {"uuid": change["uuid"], "class": node.get("class"), "settings": change["before"]}
            )
        snapshot = {"format": "tvheadend-mcp-snapshot-v1", "nodes": nodes}
        result = {
            "kind": kind,
            "dry_run": True,
            "ready": not blocked,
            "changes": changes,
            "blocked": blocked,
            "warnings": warnings,
            "recovery_snapshot": snapshot,
            "deletes_objects": False,
            "atomic": False,
        }
        if not blocked and changes:
            now = time.monotonic()
            plans = getattr(self, "_maintenance_plans", {})
            plans = {key: value for key, value in plans.items() if value["expires"] > now}
            if len(plans) >= 32:
                del plans[next(iter(plans))]
            plan_id = secrets.token_hex(16)
            plans[plan_id] = {
                "expires": now + 900,
                "fingerprint": fingerprint(data),
                "recipe": recipe,
                "result": copy.deepcopy(result),
            }
            self._maintenance_plans = plans
            result.update(plan_id=plan_id, expires_in_seconds=900)
        return result

    async def _channel_merge_plan(
        self,
        data: dict[str, Any],
        target_uuid: str,
        source_uuids: list[str],
        allow_name_mismatch: bool,
    ) -> dict[str, Any]:
        target = await self._node(target_uuid)
        sources = [await self._node(key) for key in source_uuids]
        if any(node.get("class") != "channel" for node in [target, *sources]):
            raise ValueError("Select channel UUIDs")
        before = values(target)
        if not before.get("enabled"):
            raise ValueError("The retained channel must already be enabled")
        blocked: list[Any] = []
        for source in sources:
            settings = values(source)
            if not allow_name_mismatch and programme_name(
                str(settings.get("name", ""))
            ) != programme_name(str(before.get("name", ""))):
                blocked.append(
                    {
                        "uuid": source["uuid"],
                        "reason": "Names differ, possibly regional variants; verify programmes "
                        "and explicitly allow_name_mismatch",
                    }
                )
            if settings.get("bouquet"):
                blocked.append(
                    {
                        "uuid": source["uuid"],
                        "reason": "Bouquet-managed channel; change its bouquet first",
                    }
                )
        names = {str(values(n).get("name", "")) for n in sources} - {""}
        for kind in ("recordings", "autorec", "timerec"):
            for item in data[kind]:
                reference = item.get("channel_uuid", item.get("channel"))
                if reference in source_uuids or reference in names:
                    blocked.append(
                        {
                            "kind": kind,
                            "uuid": item.get("uuid"),
                            "reason": "DVR reference to a source channel; "
                            "migrate it explicitly first",
                        }
                    )
        merged = {}
        for key in ("services", "tags", "epggrab"):
            if key not in properties(target):
                if any(values(n).get(key) for n in sources):
                    blocked.append(f"Retained channel cannot preserve {key}")
                continue
            combined = list(
                dict.fromkeys(
                    [
                        *before.get(key, []),
                        *(value for node in sources for value in values(node).get(key, [])),
                    ]
                )
            )
            if combined != before.get(key, []):
                merged[key] = combined
        if not merged.get("services", before.get("services")):
            blocked.append("The retained channel would have no services")
        changes = [{"uuid": target_uuid, "settings": merged}] if merged else []
        changes.extend(
            {"uuid": node["uuid"], "settings": {"enabled": False}}
            for node in sources
            if values(node).get("enabled")
        )
        return await self._store_plan(
            "channel_merge",
            {
                "target_uuid": target_uuid,
                "source_uuids": source_uuids,
                "allow_name_mismatch": allow_name_mismatch,
            },
            data,
            changes,
            blocked,
            [
                "Retained name/number are preserved; source channels remain stored but disabled. "
                "EPG timing/priority/icon fields are not overwritten."
            ],
        )

    async def plan_channel_merge(
        self, target_uuid: str, source_uuids: list[str], allow_name_mismatch: bool = False
    ) -> dict[str, Any]:
        """Preview an explicit channel merge, retaining target name/number and unioning mappings.

        Source channels are disabled, never deleted. DVR references and bouquet-managed
        sources block the plan. Regional/name mismatches require explicit verification.
        Returns a 15-minute plan_id for apply_maintenance_plan; does not change configuration.
        """
        target_uuid = uuid_hex(target_uuid)
        sources = list(dict.fromkeys(uuid_hex(u) for u in source_uuids))
        if not sources or target_uuid in sources or len(sources) > 50:
            raise ValueError("Select 1..50 distinct source channels, excluding the target")
        async with self._write_lock:
            return await self._channel_merge_plan(
                await self._maintenance_inventory(), target_uuid, sources, allow_name_mismatch
            )

    async def _network_plan(
        self, data: dict[str, Any], target_uuid: str, source_uuids: list[str]
    ) -> dict[str, Any]:
        target = await self._node(target_uuid)
        sources = [await self._node(key) for key in source_uuids]
        cls = target.get("class", "")
        if cls not in {"dvb_network_dvbs", "dvb_network_dvbc", "dvb_network_dvbt"}:
            raise ValueError("Consolidation supports DVB-S/C/T networks of the same class")
        target_settings = values(target)
        if not target_settings.get("enabled"):
            raise ValueError("The target network must already be enabled and scanned")
        for node in sources:
            if node.get("class") != cls:
                raise ValueError("Cannot consolidate different delivery systems")
            if cls.endswith("dvbs") and (
                target_settings.get("orbital_pos") in {None, "", 0, "0", "None"}
                or values(node).get("orbital_pos") != target_settings["orbital_pos"]
            ):
                raise ValueError("Satellite networks must have the same known orbital position")
        muxes = {m["uuid"]: m for m in data["muxes"]}
        services = {s["uuid"]: s for s in data["services"]}
        blocked: list[Any] = []

        def identity(service: dict[str, Any]) -> tuple[int, int, int] | None:
            mux = muxes.get(service.get("multiplex_uuid"), {})
            parts = (mux.get("onid"), mux.get("tsid"), service.get("sid"))
            if all(type(v) is int and 0 <= v < 65535 for v in parts) and parts[2] > 0:
                return parts
            return None

        targets: dict[tuple[int, int, int], list[str]] = {}
        for key, service in services.items():
            mux = muxes.get(service.get("multiplex_uuid"), {})
            stable_id = identity(service)
            if (
                mux.get("network_uuid") == target_uuid
                and service.get("enabled")
                and mux.get("enabled") == 1
                and stable_id
            ):
                targets.setdefault(stable_id, []).append(key)
        source_services = {
            key
            for key, service in services.items()
            if muxes.get(service.get("multiplex_uuid"), {}).get("network_uuid") in source_uuids
        }
        changes = []
        replacements = {}
        for channel in data["channels"]:
            node = await self._node(channel["uuid"])
            settings = values(node)
            current = settings.get("services", [])
            if not source_services.intersection(current):
                continue
            assigned = []
            for key in current:
                if key in source_services:
                    matches = targets.get(identity(services[key]), [])
                    if len(matches) != 1:
                        blocked.append(
                            {
                                "channel_uuid": channel["uuid"],
                                "service_uuid": key,
                                "reason": "Missing/ambiguous target ONID/TSID/SID; "
                                "scan and verify target first",
                            }
                        )
                        assigned.append(key)
                        continue
                    replacements[key] = matches[0]
                    key = matches[0]
                if key not in assigned:
                    assigned.append(key)
            if assigned != current:
                if settings.get("bouquet"):
                    blocked.append(
                        {
                            "channel_uuid": channel["uuid"],
                            "reason": "Bouquet-managed channel; change bouquet first",
                        }
                    )
                changes.append({"uuid": node["uuid"], "settings": {"services": assigned}})
        assignments = []
        for item in data["hardware"]:
            for field, current in network_fields(values(item)).items():
                if set(current).intersection(source_uuids):
                    assigned = list(
                        dict.fromkeys(
                            target_uuid if key in source_uuids else key for key in current
                        )
                    )
                    assignments.append((item["uuid"], field, assigned))
        patches: dict[str, dict[str, Any]] = {}
        for key, field, assigned in assignments:
            patches.setdefault(key, {})[field] = assigned
        changes = [{"uuid": key, "settings": patch} for key, patch in patches.items()] + changes
        changes.extend(
            {"uuid": n["uuid"], "settings": {"enabled": False}}
            for n in sources
            if values(n).get("enabled")
        )
        warnings = [
            "Muxes/services are not moved or deleted. Source databases remain stored for recovery.",
            f"{len(source_services - replacements.keys())} source services are not remapped "
            "(including unmapped services).",
            "Only broadcast IDs establish matches; verify target reception before applying.",
        ]
        return await self._store_plan(
            "network_consolidation",
            {
                "target_uuid": target_uuid,
                "source_uuids": source_uuids,
            },
            data,
            changes,
            blocked,
            warnings,
        )

    async def plan_network_consolidation(
        self, target_uuid: str, source_uuids: list[str]
    ) -> dict[str, Any]:
        """Preview migration to an existing scanned DVB network, preserving channel UUIDs.

        Reassign tuner inputs, replace mapped services only by unique ONID/TSID/SID matches,
        then disable source networks. No object deletion or unsupported mux movement.
        Different delivery systems/satellite positions and missing matches block execution.
        """
        target_uuid = uuid_hex(target_uuid)
        sources = list(dict.fromkeys(uuid_hex(u) for u in source_uuids))
        if not sources or target_uuid in sources or len(sources) > 20:
            raise ValueError("Select 1..20 source networks, excluding the target")
        async with self._write_lock:
            return await self._network_plan(
                await self._maintenance_inventory(), target_uuid, sources
            )

    async def _rollback_maintenance(self, attempted: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for change in reversed(attempted):
            try:
                node = await self._node(change["uuid"])
                current = values(node)
                if all(current.get(k) == value for k, value in change["before"].items()):
                    result.append({"uuid": change["uuid"], "status": "unchanged"})
                elif all(current.get(k) == value for k, value in change["settings"].items()):
                    await self._update(node, change["before"], False)
                    restored = values(await self._node(change["uuid"]))
                    if any(restored.get(k) != v for k, v in change["before"].items()):
                        raise TvheadendError("Rollback readback differs")
                    result.append({"uuid": change["uuid"], "status": "restored"})
                else:
                    result.append(
                        {
                            "uuid": change["uuid"],
                            "status": "conflict",
                            "error": "State changed externally; not overwritten",
                        }
                    )
            except (TvheadendError, ValueError) as exc:
                result.append({"uuid": change["uuid"], "status": "failed", "error": str(exc)})
        return result

    async def apply_maintenance_plan(self, plan_id: str, dry_run: bool = True) -> dict[str, Any]:
        """Apply a reviewed plan from this MCP process; previews by default.

        Plans expire after 15 minutes and actual executions consume the ID. Invented,
        expired or stale plans are rejected. Rechecks inventory and node fields, refuses
        active inputs/subscriptions, and attempts guarded rollback on a partial failure.
        Keep the returned recovery_snapshot; this is not an atomic transaction/full backup.
        """
        async with self._write_lock:
            saved = getattr(self, "_maintenance_plans", {}).get(plan_id)
            if not saved or saved["expires"] <= time.monotonic():
                raise ValueError("Unknown or expired plan; generate and review a new plan")
            if not dry_run and self.client.settings.read_only:
                raise TvheadendError("Writes are disabled by TVH_READ_ONLY=true")
            current = await self._maintenance_inventory()
            if fingerprint(current) != saved["fingerprint"]:
                raise ValueError("Inventory changed; generate and review a fresh plan")
            result = copy.deepcopy(saved["result"])
            for change in result["changes"]:
                node = await self._node(change["uuid"])
                if any(values(node).get(k) != v for k, v in change["before"].items()):
                    raise ValueError("Node changed since preview; generate a new plan")
                validate_fields(node, change["settings"])
            if dry_run:
                return {**result, "plan_id": plan_id}
            inputs, subscriptions = await asyncio.gather(
                self.client.request("status/inputs"), self.client.request("status/subscriptions")
            )
            if active_inputs(inputs) or rows(subscriptions):
                raise TvheadendError(
                    "Tvheadend has active inputs/subscriptions; retry maintenance while idle"
                )
            now = time.time()
            upcoming = await inventory(self.client, "dvr/entry/grid_upcoming")
            if any(
                not isinstance(item.get("start"), (int, float))
                or not isinstance(item.get("start_extra"), (int, float))
                or item["start_extra"] < 0
                or item["start"] - item["start_extra"] * 60 <= now + 120
                for item in upcoming
            ):
                raise TvheadendError(
                    "A recording is active/due soon or padding is unknown; maintenance refused"
                )
            del self._maintenance_plans[plan_id]
            attempted = []
            try:
                for change in result["changes"]:
                    node = await self._node(change["uuid"])
                    if any(values(node).get(k) != v for k, v in change["before"].items()):
                        raise TvheadendError("Concurrent node change detected")
                    attempted.append(change)
                    await self._update(node, change["settings"], False)
                    after = values(await self._node(change["uuid"]))
                    if any(after.get(k) != v for k, v in change["settings"].items()):
                        raise TvheadendError("Save readback differs from planned settings")
                return {**result, "dry_run": False, "complete": True}
            except asyncio.CancelledError:
                await asyncio.shield(self._rollback_maintenance(attempted))
                raise
            except (TvheadendError, ValueError) as exc:
                rollback = await self._rollback_maintenance(attempted)
                return {
                    **result,
                    "dry_run": False,
                    "complete": False,
                    "error": str(exc),
                    "rollback": rollback,
                    "rollback_complete": all(
                        i["status"] in {"restored", "unchanged"} for i in rollback
                    ),
                }
