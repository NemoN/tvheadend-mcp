"""Regression cases for EPG management, maintenance and bounded reception probes."""

import asyncio
import copy
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from tvheadend_mcp.admin import TvheadendAdmin
from tvheadend_mcp.client import TvheadendClient, TvheadendError
from tvheadend_mcp.config import Settings
from tvheadend_mcp.epg import coverage
from tvheadend_mcp.operations import inventory
from tvheadend_mcp.reception import TransportStats, counter_changes, input_sample
from tvheadend_mcp.schema import values

from .conftest import (
    CHANNEL,
    NETWORK,
    OTHER_NETWORK,
    OTHER_SERVICE,
    SAT,
    SERVICE,
    TUNER,
    FakeTVH,
    node,
    prop,
)

MUX = "a" * 32
OLD_MUX = "b" * 32
SOURCE_CHANNEL = "c" * 32
SECOND_TUNER = "d" * 32
MODULE = "e" * 32
TAG = "f" * 32


def packet(cc=0, *, pid=256, tei=False, scrambled=False, discontinuity=False):
    header = bytes(
        [
            0x47,
            (pid >> 8) | (0x80 if tei else 0),
            pid & 255,
            (0x30 if discontinuity else 0x10) | cc | (0x80 if scrambled else 0),
        ]
    )
    return header + (b"\x01\x80" if discontinuity else b"") + bytes(182 if discontinuity else 184)


class TestStream(httpx.AsyncByteStream):
    __test__ = False

    def __init__(self, fake):
        self.fake = fake

    async def __aiter__(self):
        self.fake.streaming = True
        for batch in range(8):
            await asyncio.sleep(self.fake.stream_delay)
            yield b"".join(packet((batch * 32 + n) % 16) for n in range(32))

    async def aclose(self):
        self.fake.streaming = False
        self.fake.stream_closed = True


class OperationTVH(FakeTVH):
    def __init__(self):
        super().__init__()
        self.streaming = False
        self.stream_closed = False
        self.stream_delay = 0
        self.status_inputs = []
        self.status_subscriptions = []
        self.nodes[SECOND_TUNER] = node(
            SECOND_TUNER, "satip_frontend_dvbs", [prop("enabled", "bool", True)], leaf=1
        )
        self.nodes[OTHER_NETWORK]["params"].append(prop("orbital_pos", "str", "19.2E"))
        self.nodes[MODULE] = node(
            MODULE, "epggrab_mod_ota", [prop("enabled", "bool", True), prop("priority", "int", 1)]
        )
        self.nodes[CHANNEL]["params"].extend(
            [
                prop("number", "s64", 1),
                prop("tags", "str", [TAG], list=1),
                prop(
                    "epggrab",
                    "str",
                    ["eit|old"],
                    list=1,
                    enum=[{"key": "eit|old"}, {"key": "xmltv|new"}],
                ),
            ]
        )
        self.nodes[SOURCE_CHANNEL] = node(
            SOURCE_CHANNEL,
            "channel",
            [
                prop("name", "str", "Das Erste"),
                prop("number", "s64", 101),
                prop("enabled", "bool", True),
                prop("services", "str", [SERVICE], list=1),
                prop("tags", "str", [], list=1),
                prop("epggrab", "str", ["xmltv|new"], list=1),
            ],
        )
        self.grids = {
            "mpegts/network/grid": [{"uuid": NETWORK}, {"uuid": OTHER_NETWORK}],
            "mpegts/mux/grid": [
                {"uuid": MUX, "network_uuid": NETWORK, "onid": 1, "tsid": 2, "enabled": 1},
                {
                    "uuid": OLD_MUX,
                    "network_uuid": OTHER_NETWORK,
                    "onid": 1,
                    "tsid": 2,
                    "enabled": 1,
                },
            ],
            "mpegts/service/grid": [
                {
                    "uuid": SERVICE,
                    "multiplex_uuid": MUX,
                    "sid": 3,
                    "enabled": True,
                    "multiplex": "Example mux",
                    "network": "Example network",
                },
                {"uuid": OTHER_SERVICE, "multiplex_uuid": OLD_MUX, "sid": 3, "enabled": True},
            ],
            "epggrab/channel/grid": [{"uuid": "0" * 32, "id": "xmltv|new"}],
            "epg/events/grid": [],
            "dvr/entry/grid": [],
            "dvr/entry/grid_upcoming": [],
            "dvr/autorec/grid": [],
            "dvr/timerec/grid": [],
        }
        self.epg = node(
            "0" * 32,
            "epggrab",
            [
                prop("cron", "str", "4 */12 * * *"),
                prop("ota_initial", "bool", True),
                prop("readonly", "int", 1, rdonly=True),
            ],
        )

    def handler(self, request):
        if "/stream/service/" in request.url.path:
            self.requests.append(("GET", "stream/service", dict(request.url.params)))
            return httpx.Response(
                200, stream=TestStream(self), headers={"Content-Type": "video/mp2t"}
            )
        path = request.url.path.split("/api/", 1)[1]
        params = (
            dict(request.url.params)
            if request.method == "GET"
            else {k: vals[0] for k, vals in parse_qs(request.content.decode()).items()}
        )
        result = None
        if path == "hardware/tree" and params["uuid"] == "root":
            result = [copy.deepcopy(self.nodes[TUNER]), copy.deepcopy(self.nodes[SECOND_TUNER])]
        elif path == "channel/grid":
            result = {
                "entries": [
                    {"uuid": k, **values(v)}
                    for k, v in self.nodes.items()
                    if v["class"] == "channel"
                ],
                "total": 2,
            }
        elif path in self.grids:
            items = self.grids[path]
            start, limit = int(params.get("start", 0)), int(params.get("limit", 1000))
            result = {"entries": copy.deepcopy(items[start : start + limit]), "total": len(items)}
        elif path == "epggrab/config/load":
            result = {"entries": [copy.deepcopy(self.epg)]}
        elif path == "epggrab/config/save":
            patch = json.loads(params["node"])
            for field in self.epg["params"]:
                if field["id"] in patch:
                    field["value"] = patch[field["id"]]
            result = {}
        elif path == "epggrab/module/list":
            result = {"entries": [{"uuid": MODULE, "title": "EIT", "status": "epggrabmodEnabled"}]}
        elif path == "service/streams":
            result = {"streams": [{"type": "H264", "pid": 256}]}
        elif path in {"epggrab/ota/trigger", "epggrab/internal/rerun"}:
            result = {}
        elif path == "status/inputs":
            result = {
                "entries": (
                    [
                        {
                            "uuid": MUX,
                            "input": "Example tuner",
                            "subs": 1,
                            "stream": "Example mux in Example network",
                            "cc": 0,
                            "te": 0,
                        }
                    ]
                    if self.streaming
                    else self.status_inputs
                )
            }
        elif path == "status/subscriptions":
            result = {
                "entries": [{"id": 1, "title": "HTTP"}]
                if self.streaming
                else self.status_subscriptions
            }
        if result is not None:
            self.requests.append((request.method, path, params))
            return httpx.Response(200, json=result)
        return super().handler(request)


@pytest.fixture
async def ops(tmp_path):
    fake = OperationTVH()
    client = TvheadendClient(
        Settings("http://tvh.test:9981/tvh", auth="none", state_dir=tmp_path),
        transport=httpx.MockTransport(fake.handler),
    )
    admin = TvheadendAdmin(client)
    yield admin, fake
    await client.close()


def posts(fake):
    return [r for r in fake.requests if r[0] == "POST"]


async def test_epg_simple_config_schema_save_and_readback(ops):
    admin, fake = ops
    preview = await admin.update_epg_config({"ota_initial": False}, dry_run=True)
    assert preview["before"] == {"ota_initial": True}
    assert not posts(fake)
    result = await admin.update_epg_config({"ota_initial": False})
    assert posts(fake)[-1][1:] == ("epggrab/config/save", {"node": '{"ota_initial":false}'})
    assert values(result["after"])["ota_initial"] is False


@pytest.mark.parametrize("settings", [{"typo": 1}, {"readonly": 2}, {"ota_initial": "false"}])
async def test_epg_unknown_readonly_and_wrong_types_never_write(ops, settings):
    admin, fake = ops
    with pytest.raises(ValueError):
        await admin.update_epg_config(settings)
    assert not posts(fake)


async def test_epg_module_must_be_installed(ops):
    admin, fake = ops
    with pytest.raises(ValueError, match="installed"):
        await admin.update_epg_module(CHANNEL, {"enabled": False})
    assert not posts(fake)
    await admin.update_epg_module(MODULE, {"enabled": False})
    assert values(fake.nodes[MODULE])["enabled"] is False


@pytest.mark.parametrize(
    "kind,delay,path,params",
    [
        ("ota", 60, "epggrab/ota/trigger", {"trigger": "60"}),
        ("internal", 1, "epggrab/internal/rerun", {"rerun": "1"}),
    ],
)
async def test_epg_trigger_native_parameters(ops, kind, delay, path, params):
    admin, fake = ops
    await admin.trigger_epg_grab(kind, delay, dry_run=True)
    assert not fake.requests
    await admin.trigger_epg_grab(kind, delay)
    assert fake.requests[-1] == ("POST", path, params)


@pytest.mark.parametrize("kind,delay", [("ota", 0), ("ota", 604801), ("internal", 5), ("typo", 1)])
async def test_epg_invalid_trigger_never_writes(ops, kind, delay):
    admin, fake = ops
    with pytest.raises(ValueError):
        await admin.trigger_epg_grab(kind, delay)
    assert not fake.requests


async def test_epg_sources_use_enum_string_keys_and_preserve_existing(ops):
    admin, fake = ops
    result = await admin.assign_epg_sources(CHANNEL, ["xmltv|new"])
    assert values(result["after"])["epggrab"] == ["eit|old", "xmltv|new"]
    with pytest.raises(ValueError, match="Unknown EPG"):
        await admin.assign_epg_sources(CHANNEL, ["invented"])
    assert len(posts(fake)) == 1
    result = await admin.assign_epg_sources(CHANNEL, ["eit|old"], mode="remove")
    assert values(result["after"])["epggrab"] == ["xmltv|new"]


def test_coverage_clips_overlaps_and_reports_real_gaps():
    report = coverage(
        [
            {"start": -100, "stop": 2400, "summary": "Text"},
            {"start": 1200, "stop": 3000},
            {"start": 3500, "stop": 4000},
        ],
        0,
        3600,
    )
    assert report["covered_seconds"] == 3100
    assert report["largest_gap_seconds"] == 500
    assert report["described_events"] == 1
    assert report["gaps"] == [{"start": 3000, "stop": 3500, "seconds": 500}]


async def test_epg_coverage_keeps_numeric_filters_and_reports_no_events(ops):
    admin, fake = ops
    result = await admin.epg_coverage([CHANNEL], hours=1)
    assert result["complete"]
    assert result["channels_without_events"] == [CHANNEL]
    assert result["channels"][0]["largest_gap_seconds"] == 3600
    params = next(p for _, path, p in fake.requests if path == "epg/events/grid")
    assert {f["field"] for f in json.loads(params["filter"])} == {"start", "stop"}


async def test_inventory_reads_every_page_and_refuses_repeated_pages(ops):
    admin, fake = ops
    fake.grids["epg/events/grid"] = [{"eventId": n} for n in range(1001)]
    result = await inventory(admin.client, "epg/events/grid")
    assert len(result) == 1001
    assert [p["start"] for _, path, p in fake.requests if path == "epg/events/grid"] == [
        "0",
        "1000",
    ]
    fake.grids["epg/events/grid"][-1]["eventId"] = 0
    with pytest.raises(TvheadendError, match="pagination repeated"):
        await inventory(admin.client, "epg/events/grid")


@pytest.mark.parametrize("response", [{}, {"entries": [{"eventId": n} for n in range(1001)]}])
async def test_inventory_refuses_missing_entries_or_ignored_page_limits(ops, monkeypatch, response):
    admin, _ = ops

    async def malformed(*args, **kwargs):
        return response

    monkeypatch.setattr(admin.client, "grid", malformed)
    with pytest.raises(TvheadendError):
        await inventory(admin.client, "epg/events/grid")


async def test_channel_audit_keeps_regional_names_separate(ops):
    admin, fake = ops
    result = await admin.audit_channels()
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["suggested_target"] == CHANNEL
    fake.nodes[SOURCE_CHANNEL]["params"][0]["value"] = "NDR regional"
    assert not (await admin.audit_channels())["candidates"]
    assert not posts(fake)


async def test_channel_plan_requires_review_and_preserves_target(ops):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    assert plan["ready"] and plan["plan_id"]
    assert not posts(fake)
    await admin.apply_maintenance_plan(plan["plan_id"])
    assert not posts(fake)
    result = await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert result["complete"]
    assert values(fake.nodes[CHANNEL])["number"] == 1
    assert values(fake.nodes[CHANNEL])["services"] == [OTHER_SERVICE, SERVICE]
    assert values(fake.nodes[CHANNEL])["epggrab"] == ["eit|old", "xmltv|new"]
    assert values(fake.nodes[SOURCE_CHANNEL])["enabled"] is False
    assert SOURCE_CHANNEL in fake.nodes
    with pytest.raises(ValueError, match="expired"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)


@pytest.mark.parametrize("kind", ["dvr/entry/grid", "dvr/autorec/grid", "dvr/timerec/grid"])
async def test_channel_plan_blocks_dvr_references(ops, kind):
    admin, fake = ops
    fake.grids[kind] = [{"uuid": "0" * 32, "channel_uuid": SOURCE_CHANNEL}]
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    assert not plan["ready"] and "plan_id" not in plan
    assert not posts(fake)


async def test_channel_plan_blocks_unverified_regional_mismatch(ops):
    admin, fake = ops
    fake.nodes[SOURCE_CHANNEL]["params"][0]["value"] = "Different region"
    assert not (await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL]))["ready"]
    assert (await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL], allow_name_mismatch=True))[
        "ready"
    ]


async def test_stale_and_expired_plans_cannot_apply(ops):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    fake.nodes[SOURCE_CHANNEL]["params"][0]["value"] = "Changed externally"
    with pytest.raises(ValueError, match="Inventory changed"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    admin._maintenance_plans[plan["plan_id"]]["expires"] = 0
    with pytest.raises(ValueError, match="expired"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert not posts(fake)


async def test_maintenance_blocks_busy_and_imminent_dvr(ops):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    fake.status_subscriptions = [{"id": 42}]
    with pytest.raises(TvheadendError, match="active"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    fake.status_subscriptions = []
    fake.grids["dvr/entry/grid_upcoming"] = [{"start": time.time() + 60}]
    with pytest.raises(TvheadendError, match="due soon"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert not posts(fake)


async def test_maintenance_failure_rolls_back_prior_writes(ops):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    fake.fail_write_for = SOURCE_CHANNEL
    result = await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert not result["complete"] and result["rollback_complete"]
    assert values(fake.nodes[CHANNEL])["services"] == [OTHER_SERVICE]
    assert values(fake.nodes[SOURCE_CHANNEL])["enabled"] is True


async def test_network_plan_uses_broadcast_ids_and_retires_without_deleting(ops):
    admin, fake = ops
    plan = await admin.plan_network_consolidation(NETWORK, [OTHER_NETWORK])
    assert plan["ready"] and not posts(fake)
    result = await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert result["complete"]
    assert values(fake.nodes[CHANNEL])["services"] == [SERVICE]
    assert values(fake.nodes[SAT])["networks"] == [NETWORK]
    assert values(fake.nodes[OTHER_NETWORK])["enabled"] is False
    assert OTHER_NETWORK in fake.nodes
    assert all(path != "idnode/delete" for _, path, _ in fake.requests)


async def test_missing_target_service_blocks_network_retirement(ops):
    admin, fake = ops
    fake.grids["mpegts/service/grid"][0]["sid"] = 99
    plan = await admin.plan_network_consolidation(NETWORK, [OTHER_NETWORK])
    assert not plan["ready"] and "plan_id" not in plan
    assert plan["blocked"][0]["service_uuid"] == OTHER_SERVICE
    assert not posts(fake)


@pytest.mark.parametrize(
    "delivery,position", [("dvb_network_dvbc", "19.2E"), ("dvb_network_dvbs", "13E")]
)
async def test_network_plan_refuses_cross_system_and_cross_satellite(ops, delivery, position):
    admin, fake = ops
    fake.nodes[OTHER_NETWORK]["class"] = delivery
    fake.nodes[OTHER_NETWORK]["params"][-1]["value"] = position
    with pytest.raises(ValueError):
        await admin.plan_network_consolidation(NETWORK, [OTHER_NETWORK])
    assert not posts(fake)


def test_ts_parser_handles_split_packets_duplicates_and_discontinuity():
    stats = TransportStats()
    content = packet(0) + packet(1) + packet(1) + packet(9, discontinuity=True) + packet(10)
    for index in range(0, len(content), 137):
        stats.feed(content[index : index + 137])
    assert stats.packets == 5 and stats.duplicates == 1
    assert stats.continuity_errors == stats.discarded_bytes == 0


def test_ts_parser_detects_loss_tei_and_scrambling():
    stats = TransportStats()
    stats.feed(b"garbage" + packet(0) + packet(2, tei=True, scrambled=True))
    assert stats.discarded_bytes == 7 and stats.continuity_errors == 1
    assert stats.tei == stats.scrambled == 1


def test_signal_units_do_not_treat_db_as_percent():
    result = input_sample({"signal": -67000, "signal_scale": 2, "snr": 32767, "snr_scale": 1})
    assert result["signal_db"] == -67
    assert 49 < result["snr_percent"] < 51
    assert "signal_percent" not in result


def test_input_counter_deltas_detect_resets_and_do_not_subtract_unrelated_inputs():
    report = counter_changes(
        [
            {"uuid": MUX, "cc": 5, "te": 7},
            {"uuid": OLD_MUX, "cc": 99},
            {"uuid": MUX, "cc": 8, "te": 1},
        ]
    )
    assert report[0]["counters"]["cc"]["delta"] == 3
    assert report[0]["counters"]["te"] == {"delta": None, "counter_reset": True}
    assert report[1]["counters"]["cc"]["delta"] is None


async def test_incomplete_epg_read_is_reported_instead_of_claiming_coverage(ops, monkeypatch):
    admin, _ = ops
    original = admin.client.grid

    async def truncated(path, **kwargs):
        if path == "epg/events/grid":
            return {"entries": [], "total": 1001}
        return await original(path, **kwargs)

    monkeypatch.setattr(admin.client, "grid", truncated)
    result = await admin.epg_coverage([CHANNEL])
    assert not result["complete"] and not result["channels"][0]["complete"]
    assert "coverage_percent" not in result["channels"][0]


async def test_maintenance_does_not_overwrite_external_changes_during_rollback(ops, monkeypatch):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    original = admin._update

    async def save(node, settings, dry_run):
        if node["uuid"] == SOURCE_CHANNEL:
            for field in fake.nodes[CHANNEL]["params"]:
                if field["id"] == "services":
                    field["value"] = []
            raise TvheadendError("Other operator changed the target")
        return await original(node, settings, dry_run)

    monkeypatch.setattr(admin, "_update", save)
    result = await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert not result["rollback_complete"]
    assert values(fake.nodes[CHANNEL])["services"] == []
    assert any(item["status"] == "conflict" for item in result["rollback"])


async def test_readonly_allows_maintenance_preview_but_blocks_execution(ops):
    admin, fake = ops
    plan = await admin.plan_channel_merge(CHANNEL, [SOURCE_CHANNEL])
    admin.client.settings = replace(admin.client.settings, read_only=True)
    assert (await admin.apply_maintenance_plan(plan["plan_id"]))["dry_run"]
    with pytest.raises(TvheadendError, match="disabled"):
        await admin.apply_maintenance_plan(plan["plan_id"], dry_run=False)
    assert not posts(fake)


async def test_tuner_probe_refuses_unknown_dvr_padding(ops):
    admin, fake = ops
    fake.grids["dvr/entry/grid_upcoming"] = [
        {
            "start": time.time() + 10000,
            "stop": time.time() + 11000,
            "start_extra": -1,
        }
    ]
    with pytest.raises(TvheadendError, match="padding is unknown"):
        await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert not posts(fake)


async def test_probe_byte_limit_and_timeout_always_close_stream(ops):
    admin, fake = ops
    capped = await admin.probe_reception(SERVICE, max_bytes=18800, dry_run=False)
    assert capped["bytes"] == 18800 and capped["stopped_by"] == "byte_limit"
    assert fake.stream_closed
    fake.stream_delay = 0.8
    timed = await admin.probe_reception(SERVICE, duration_seconds=1, dry_run=False)
    assert timed["stopped_by"] == "duration" and fake.stream_closed


async def test_valid_null_packets_without_media_do_not_pass_a_service_probe(ops, monkeypatch):
    admin, _ = ops
    original = admin.client.request

    async def no_media(path, *args, **kwargs):
        if path == "service/streams":
            return {"streams": [{"type": "H264", "pid": 999}]}
        return await original(path, *args, **kwargs)

    monkeypatch.setattr(admin.client, "request", no_media)
    result = await admin.probe_reception(SERVICE, dry_run=False)
    assert result["packets"] > 100 and not result["media_verified"] and not result["passed"]


async def test_probe_dry_run_does_not_stream_or_modify_tuners(ops):
    admin, fake = ops
    result = await admin.probe_reception(SERVICE, tuner_uuid=TUNER)
    assert result["temporary_changes"][0]["uuid"] == SECOND_TUNER
    assert not posts(fake)
    assert not any(path == "stream/service" for _, path, _ in fake.requests)
    assert not list(admin.client.settings.state_dir.iterdir())


async def test_probe_closes_stream_and_restores_isolated_tuners(ops):
    admin, fake = ops
    result = await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert result["passed"] and result["tuner_verified"]
    assert fake.stream_closed and result["restoration_complete"]
    assert values(fake.nodes[SECOND_TUNER])["enabled"] is True
    assert not (await admin.reception_recoveries())["entries"]


async def test_probe_rejects_active_server_before_any_isolation(ops):
    admin, fake = ops
    fake.status_inputs = [{"subs": 1}]
    with pytest.raises(TvheadendError, match="idle server"):
        await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert not posts(fake)


async def test_tuner_probe_cannot_claim_iptv_reception_is_from_a_dvb_tuner(ops):
    admin, fake = ops
    fake.nodes[NETWORK]["class"] = "iptv_network"
    with pytest.raises(ValueError, match="matching DVB"):
        await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert not posts(fake)


async def test_probe_restores_tuners_on_stream_failure(ops, monkeypatch):
    admin, fake = ops

    async def fail(*args):
        raise TvheadendError("Stream failed")

    monkeypatch.setattr(admin, "_receive_probe", fail)
    result = await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert not result["passed"] and result["restoration_complete"]
    assert values(fake.nodes[SECOND_TUNER])["enabled"] is True


async def test_probe_cancellation_restores_tuners_and_removes_journal(ops, monkeypatch):
    admin, fake = ops
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(admin, "_receive_probe", wait)
    task = asyncio.create_task(admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert values(fake.nodes[SECOND_TUNER])["enabled"] is True
    assert not (await admin.reception_recoveries())["entries"]


async def test_failed_probe_restore_leaves_private_recovery_journal(ops, monkeypatch):
    admin, fake = ops

    async def fail(*args):
        fake.fail_write_for = SECOND_TUNER
        raise TvheadendError("Stream failed")

    monkeypatch.setattr(admin, "_receive_probe", fail)
    result = await admin.probe_reception(SERVICE, tuner_uuid=TUNER, dry_run=False)
    assert not result["restoration_complete"]
    journals = (await admin.reception_recoveries())["entries"]
    assert len(journals) == 1
    path = admin.client.settings.state_dir / journals[0]["path"].split("/")[-1]
    assert path.stat().st_mode & 0o777 == 0o600


async def test_readonly_blocks_new_writes_and_active_get_probe(ops):
    admin, fake = ops
    admin.client.settings = replace(admin.client.settings, read_only=True)
    for call in (
        admin.update_epg_config({"ota_initial": False}),
        admin.trigger_epg_grab(),
        admin.probe_reception(SERVICE, dry_run=False),
    ):
        with pytest.raises(TvheadendError):
            await call
    assert not posts(fake)
    assert not any(path == "stream/service" for _, path, _ in fake.requests)
