"""Bounded MPEG-TS reception probes with optional guarded tuner isolation."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .client import TvheadendError
from .maintenance import flatten
from .operations import active_inputs, inventory, rows
from .schema import uuid_hex, validate_fields, values


class TransportStats:
    """Consume 188-byte TS packets without retaining programme content."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.previous: dict[int, tuple[int, bytes]] = {}
        self.pid_packets: dict[int, int] = {}
        self.bytes = self.packets = self.tei = self.scrambled = 0
        self.continuity_errors = self.discarded_bytes = self.duplicates = 0
        self.invalid_packets = 0

    def feed(self, data: bytes) -> None:
        self.bytes += len(data)
        self.buffer.extend(data)
        offset = 0
        while len(self.buffer) - offset >= 188:
            if self.buffer[offset] != 0x47 or (
                len(self.buffer) - offset >= 376 and self.buffer[offset + 188] != 0x47
            ):
                offset += 1
                self.discarded_bytes += 1
                continue
            packet = bytes(self.buffer[offset : offset + 188])
            offset += 188
            self.packets += 1
            self.tei += bool(packet[1] & 0x80)
            self.scrambled += bool(packet[3] & 0xC0)
            pid = ((packet[1] & 31) << 8) | packet[2]
            self.pid_packets[pid] = self.pid_packets.get(pid, 0) + 1
            adaptation = (packet[3] >> 4) & 3
            cc = packet[3] & 15
            if adaptation == 0 or (adaptation & 2 and packet[4] > 183):
                self.invalid_packets += 1
                continue
            if adaptation & 2 and packet[4] and packet[4] <= 183 and packet[5] & 0x80:
                self.previous.pop(pid, None)
            if pid != 8191 and adaptation & 1:
                old = self.previous.get(pid)
                if old is not None:
                    if cc == old[0] and packet == old[1]:
                        self.duplicates += 1
                    elif cc != (old[0] + 1) % 16:
                        self.continuity_errors += 1
                self.previous[pid] = (cc, packet)
        del self.buffer[:offset]

    def report(self) -> dict[str, int]:
        return {
            key: getattr(self, key)
            for key in (
                "bytes",
                "packets",
                "tei",
                "scrambled",
                "continuity_errors",
                "discarded_bytes",
                "duplicates",
                "invalid_packets",
            )
        }


def input_sample(row: dict[str, Any]) -> dict[str, Any]:
    """Retain signal units and cumulative errors; never invent an unsupported percentage."""
    result = {
        key: row[key]
        for key in (
            "uuid",
            "input",
            "stream",
            "bps",
            "ber",
            "cc",
            "te",
            "unc",
            "signal",
            "signal_scale",
            "snr",
            "snr_scale",
        )
        if key in row
    }
    for field in ("signal", "snr"):
        value = row.get(field)
        scale = row.get(field + "_scale")
        if isinstance(value, (int, float)) and scale in {1, 2}:
            result[field + ("_percent" if scale == 1 else "_db")] = round(
                value * 100 / 65535 if scale == 1 else value / 1000, 3
            )
    return result


def counter_changes(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(str(sample.get("uuid", sample.get("input", "unknown"))), []).append(
            sample
        )
    result = []
    for key, items in grouped.items():
        deltas = {}
        for field in ("cc", "te", "unc"):
            measurements = [s[field] for s in items if type(s.get(field)) is int]
            reset = any(a > b for a, b in zip(measurements, measurements[1:], strict=False))
            deltas[field] = {
                "delta": measurements[-1] - measurements[0]
                if len(measurements) >= 2 and not reset
                else None,
                "counter_reset": reset,
            }
        result.append({"uuid": key, "samples": len(items), "counters": deltas})
    return result


class ReceptionTools:
    def _recovery_prefix(self) -> str:
        return (
            "reception-" + hashlib.sha256(self.client.settings.url.encode()).hexdigest()[:16] + "-"
        )

    async def reception_recoveries(self) -> dict[str, Any]:
        """Read pending tuner-isolation recovery journals for this Tvheadend URL.

        Normally probes restore settings and remove journals. After a process/host crash,
        inspect the recorded snapshot and current hardware, then use restore_snapshot.
        This tool never changes settings or reads files outside the configured state folder.
        """
        folder = self.client.settings.state_dir
        records = []
        if folder.is_dir():
            for path in sorted(folder.glob(self._recovery_prefix() + "*.json"))[:100]:
                if path.is_symlink() or path.stat().st_size > 1000000:
                    continue
                try:
                    records.append({"path": str(path), **json.loads(path.read_text())})
                except (OSError, ValueError):
                    records.append({"path": str(path), "error": "Unreadable recovery journal"})
        return {"entries": records, "automatic_restore": False}

    async def _probe_idle(self, horizon: float) -> None:
        inputs, subscriptions, recordings = await asyncio.gather(
            self.client.request("status/inputs"),
            self.client.request("status/subscriptions"),
            inventory(self.client, "dvr/entry/grid_upcoming"),
        )
        if active_inputs(inputs) or rows(subscriptions):
            raise TvheadendError(
                "Tuner isolation requires an idle server; active inputs/subscriptions exist"
            )
        now = time.time()
        for item in recordings:
            start, stop = item.get("start"), item.get("stop")
            if not isinstance(start, (int, float)) or not isinstance(stop, (int, float)):
                raise TvheadendError("Cannot establish the upcoming DVR schedule")
            padding = item.get("start_extra")
            if not isinstance(padding, (int, float)) or padding < 0:
                raise TvheadendError("Upcoming DVR padding is unknown; tuner isolation refused")
            if stop >= now and start - max(0, padding) * 60 <= now + horizon + 120:
                raise TvheadendError("A recording is active or due soon; tuner isolation refused")

    async def _isolation_changes(self, tuner_uuid: str) -> list[dict[str, Any]]:
        hardware = flatten((await self.hardware_tree(recursive=True, max_depth=12))["entries"])
        frontends = [node for node in hardware if "frontend" in node.get("class", "")]
        if tuner_uuid not in {n.get("uuid", n.get("id")) for n in frontends}:
            raise ValueError("Select a frontend UUID from hardware_tree")
        if len(frontends) > 100:
            raise ValueError("Tuner isolation supports at most 100 frontends")
        changes = []
        for item in frontends:
            node = await self._node(item.get("uuid", item.get("id")))
            current = values(node)
            if type(current.get("enabled")) is not bool:
                raise ValueError("All frontends must expose a readable boolean enabled field")
            desired = node["uuid"] == tuner_uuid
            if current["enabled"] != desired:
                validate_fields(node, {"enabled": desired})
                changes.append(
                    {
                        "uuid": node["uuid"],
                        "class": node.get("class"),
                        "before": {"enabled": current["enabled"]},
                        "settings": {"enabled": desired},
                    }
                )
        return changes

    async def _receive_probe(
        self, service: dict[str, Any], seconds: int, max_bytes: int, isolated: bool
    ) -> dict[str, Any]:
        stats = TransportStats()
        samples: list[dict[str, Any]] = []
        sample_error = None
        expected = str(service.get("multiplex", "")) + " in " + str(service.get("network", ""))
        stop = asyncio.Event()
        started = time.monotonic()
        reason = "eof"

        async def sample() -> None:
            nonlocal sample_error
            while not stop.is_set():
                try:
                    inputs = active_inputs(await self.client.request("status/inputs"))
                    if isolated:
                        subs = rows(await self.client.request("status/subscriptions"))
                        if len(inputs) > 1 or len(subs) > 1:
                            raise TvheadendError(
                                "Another subscription appeared during isolated testing"
                            )
                    samples.extend(
                        input_sample(row) for row in inputs if row.get("stream") == expected
                    )
                except TvheadendError as exc:
                    sample_error = str(exc)
                    stop.set()
                    return
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
                except TimeoutError:
                    pass

        sampler = asyncio.create_task(sample())
        error = None
        try:
            url = self.client.settings.url.rstrip("/") + "/stream/service/" + service["uuid"]
            async with asyncio.timeout(seconds):
                async with self.client.http.stream(
                    "GET",
                    url,
                    params={"profile": "pass", "weight": 1},
                    headers={"User-Agent": "Tvheadend-MCP reception probe"},
                    timeout=httpx.Timeout(min(5, seconds), connect=min(5, seconds)),
                ) as response:
                    if response.status_code != 200:
                        raise TvheadendError(
                            f"Stream returned HTTP {response.status_code}; "
                            "check advanced streaming rights"
                        )
                    async for chunk in response.aiter_bytes():
                        if stop.is_set():
                            reason = "monitor_error"
                            break
                        stats.feed(chunk[: max(0, max_bytes - stats.bytes)])
                        if stats.bytes >= max_bytes:
                            reason = "byte_limit"
                            break
        except TimeoutError:
            reason = "duration"
        except httpx.RequestError:
            error = (
                "Stream connection failed or timed out; check reception and streaming permissions"
            )
        except TvheadendError as exc:
            error = str(exc)
        finally:
            stop.set()
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        elapsed = time.monotonic() - started
        counters = counter_changes(samples)
        new_input_errors = any(
            metric["delta"] is not None and metric["delta"] > 0
            for item in counters
            for metric in item["counters"].values()
        )
        media = []
        try:
            components = await self.client.request("service/streams", {"uuid": service["uuid"]})
            streams = components.get("streams", []) if isinstance(components, dict) else []
            video = {"MPEG2VIDEO", "H264", "HEVC", "MPEG4VIDEO", "VP8", "VP9", "AV1"}
            audio = {"MPEG2AUDIO", "MP2", "MP3", "AAC", "AC3", "EAC3", "AC4", "VORBIS", "OPUS"}
            media = [
                {"pid": s["pid"], "type": s["type"], "packets": stats.pid_packets.get(s["pid"], 0)}
                for s in streams
                if s.get("type") in video | audio and type(s.get("pid")) is int
            ]
        except TvheadendError as exc:
            error = error or str(exc)
        media_verified = any(s["packets"] > 0 for s in media)
        clean = stats.packets >= 100 and not any(
            (
                stats.tei,
                stats.scrambled,
                stats.continuity_errors,
                stats.discarded_bytes,
                stats.invalid_packets,
                new_input_errors,
                error,
                sample_error,
            )
        )
        return {
            **stats.report(),
            "seconds": round(elapsed, 3),
            "stopped_by": reason,
            "input_samples": samples,
            "error": error or sample_error,
            "passed": clean and media_verified,
            "media_verified": media_verified,
            "media_components": media,
            "input_counter_changes": counters,
            "scope": "transport packets; does not decode audio/video",
            "measured_bitrate_bps": round(stats.bytes * 8 / elapsed) if elapsed else 0,
        }

    async def probe_reception(
        self,
        service_uuid: str,
        duration_seconds: int = 10,
        max_bytes: int = 16777216,
        tuner_uuid: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Preview or run a bounded service MPEG-TS test (1..30 seconds, at most 64 MiB).

        Without tuner_uuid, Tvheadend selects the receiver. An explicit frontend UUID
        temporarily isolates that tuner by disabling all competing enabled frontends.
        Isolation requires an idle server/no imminent DVR, journals prior settings and
        restores them on completion, error or cancellation. Save the preview snapshot.
        Process/host crashes require inspection via reception_recoveries and explicit restore.
        Active probing is blocked in read-only mode, including GET-only stream tests.
        """
        service_uuid = uuid_hex(service_uuid)
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= 30:
            raise ValueError("duration_seconds must be between 1 and 30")
        if type(max_bytes) is not int or not 18800 <= max_bytes <= 67108864:
            raise ValueError("max_bytes must be between 18800 and 67108864")
        tuner_uuid = uuid_hex(tuner_uuid) if tuner_uuid else None
        async with self._write_lock:
            services = await inventory(self.client, "mpegts/service/grid", {"hidemode": "none"})
            service = next((s for s in services if s.get("uuid") == service_uuid), None)
            if not service or not service.get("enabled"):
                raise ValueError("Select an enabled discovered service UUID")
            if tuner_uuid:
                muxes = await inventory(self.client, "mpegts/mux/grid", {"hidemode": "none"})
                mux = next(
                    (m for m in muxes if m.get("uuid") == service.get("multiplex_uuid")), None
                )
                if not mux or not mux.get("network_uuid"):
                    raise ValueError("Cannot establish the service's DVB network")
                network = await self._node(mux["network_uuid"])
                frontend = await self._node(tuner_uuid)
                network_class = network.get("class", "")
                system = network_class.removeprefix("dvb_network_").replace("_", "")
                tuner_system = (
                    frontend.get("class", "").rsplit("_frontend_", 1)[-1].replace("_", "")
                )
                if not network_class.startswith("dvb_network_") or system != tuner_system:
                    raise ValueError(
                        "Tuner-specific probes require a matching DVB frontend/network"
                    )
            changes = await self._isolation_changes(tuner_uuid) if tuner_uuid else []
            snapshot = {
                "format": "tvheadend-mcp-snapshot-v1",
                "nodes": [
                    {"uuid": c["uuid"], "class": c["class"], "settings": c["before"]}
                    for c in changes
                ],
            }
            preview = {
                "dry_run": dry_run,
                "service_uuid": service_uuid,
                "tuner_uuid": tuner_uuid,
                "duration_seconds": duration_seconds,
                "max_bytes": max_bytes,
                "temporary_changes": changes,
                "recovery_snapshot": snapshot,
                "persistent_configuration_changes": False,
            }
            if dry_run:
                return preview
            if self.client.settings.read_only:
                raise TvheadendError("Active probing is disabled by TVH_READ_ONLY=true")
            if tuner_uuid:
                await self._probe_idle(duration_seconds)
            journal: Path | None = None
            attempted = []
            result = dict(preview)
            try:
                if changes:
                    folder = self.client.settings.state_dir
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    journal = folder / f"{self._recovery_prefix()}{time.time_ns()}.json"
                    fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as file:
                        json.dump(
                            {
                                "created_at": int(time.time()),
                                "recovery_snapshot": snapshot,
                                "temporary_changes": changes,
                            },
                            file,
                        )
                        file.flush()
                        os.fsync(file.fileno())
                for change in changes:
                    node = await self._node(change["uuid"])
                    if values(node).get("enabled") != change["before"]["enabled"]:
                        raise TvheadendError("Tuner changed during preflight; probe refused")
                    attempted.append(change)
                    await self._update(node, change["settings"], False)
                    if (
                        values(await self._node(change["uuid"])).get("enabled")
                        != change["settings"]["enabled"]
                    ):
                        raise TvheadendError("Tuner isolation readback failed")
                result.update(
                    await self._receive_probe(
                        service, duration_seconds, max_bytes, bool(tuner_uuid)
                    )
                )
                result["tuner_verified"] = bool(
                    tuner_uuid
                    and result["packets"] >= 100
                    and result["input_samples"]
                    and not result["error"]
                )
                if tuner_uuid and await self._isolation_changes(tuner_uuid):
                    result["tuner_verified"] = False
                    result["error"] = "Frontend enabled states changed externally during the probe"
                if tuner_uuid and not result["tuner_verified"]:
                    result["passed"] = False
            except (TvheadendError, ValueError, OSError) as exc:
                result.update(
                    passed=False,
                    error=str(exc)
                    if not isinstance(exc, OSError)
                    else "Cannot persist tuner recovery journal",
                )
            finally:
                restored = await asyncio.shield(self._rollback_maintenance(attempted))
                result["restoration"] = restored
                result["restoration_complete"] = all(
                    r["status"] in {"restored", "unchanged"} for r in restored
                )
                result["persistent_configuration_changes"] = not result["restoration_complete"]
                if journal and result["restoration_complete"]:
                    journal.unlink(missing_ok=True)
                elif journal:
                    result["recovery_journal"] = str(journal)
                    result["passed"] = False
            return result
