import json
from datetime import datetime

import pytest

from tvheadend_mcp.schema import validate_fields

from .conftest import (
    CHANNEL,
    LNB,
    NETWORK,
    OTHER_NETWORK,
    OTHER_SERVICE,
    RECORDING,
    SAT,
    SERVICE,
    TUNER,
)


async def test_satconf_nosave_selector_is_still_writable(admin, fake):
    result = await admin.update_node(TUNER, {"satconf": "advanced"})
    assert result["before"] == {"satconf": "simple"}
    assert result["after"]["params"][1]["value"] == "advanced"


@pytest.mark.parametrize(
    "settings", [{"typo": 1}, {"active": True}, {"satconf": "made-up"}, {"enabled": "false"}, {}]
)
async def test_invalid_configuration_never_writes(admin, fake, settings):
    with pytest.raises(ValueError):
        await admin.update_node(TUNER, settings)
    assert all(method == "GET" for method, _, _ in fake.requests)


async def test_dry_run_reads_metadata_but_does_not_write(admin, fake):
    result = await admin.update_node(TUNER, {"enabled": False}, dry_run=True)
    assert result["dry_run"]
    assert result["parameters"]["node"]["enabled"] is False
    assert all(method == "GET" for method, _, _ in fake.requests)


async def test_network_assignment_is_additive_at_satellite_child(admin, fake):
    result = await admin.assign_networks(SAT, [NETWORK, NETWORK])
    params = next(p for m, path, p in fake.requests if m == "POST" and path == "idnode/save")
    assert json.loads(params["node"]) == {"uuid": SAT, "networks": [OTHER_NETWORK, NETWORK]}
    assert result["before"] == {"networks": [OTHER_NETWORK]}


async def test_diseqc_assignment_uses_selected_port_field(admin, fake):
    await admin.assign_networks(SAT, [NETWORK], field="network_aa", mode="replace")
    patch = json.loads(next(p["node"] for m, path, p in fake.requests if m == "POST"))
    assert patch == {"uuid": SAT, "network_aa": [NETWORK]}


async def test_network_assignment_rejects_wrong_node_without_post(admin, fake):
    with pytest.raises(ValueError, match="network-assignment"):
        await admin.assign_networks(TUNER, [NETWORK])
    assert all(method == "GET" for method, _, _ in fake.requests)


async def test_unicable_settings_and_recursive_topology(admin):
    tree = await admin.hardware_tree(recursive=True)
    assert tree["entries"][0]["children"][0]["children"][0]["uuid"] == LNB
    result = await admin.update_node(LNB, {"id": 3, "frequency": 1680})
    assert result["after"]["params"][1]["value"] == 1680


async def test_maximum_tree_depth_is_reported(admin):
    tree = await admin.hardware_tree(recursive=True, max_depth=1)
    assert tree["entries"][0]["children_truncated"] is True


async def test_live_enum_descriptor_is_resolved_through_readonly_api(admin, fake):
    field = fake.nodes[SAT]["params"][0]
    field["enum"] = {"type": "api", "uri": "mpegts/input/network_list", "params": {"uuid": TUNER}}
    await admin.get_field_options(SAT, "networks")
    assert fake.requests[-1] == ("GET", "mpegts/input/network_list", {"uuid": TUNER})


async def test_successful_write_with_failed_readback_is_not_reported_as_failed_save(admin, fake):
    original = fake.handler

    def handler(request):
        result = original(request)
        if request.method == "POST" and request.url.path.endswith("idnode/save"):
            del fake.nodes[TUNER]
        return result

    admin.client.http._transport.handler = handler
    result = await admin.update_node(TUNER, {"satconf": "advanced"})
    assert result["dry_run"] is False
    assert "readback_error" in result
    assert sum(method == "POST" for method, _, _ in fake.requests) == 1


async def test_channel_mapping_preserves_existing_services(admin, fake):
    await admin.assign_services(CHANNEL, [SERVICE])
    patch = json.loads(next(p["node"] for m, path, p in fake.requests if m == "POST"))
    assert patch["services"] == [OTHER_SERVICE, SERVICE]


async def test_native_mapper_uses_save_not_nonexistent_start(admin, fake):
    result = await admin.map_services([SERVICE], {"encrypted": False, "merge_same_name": True})
    assert result["endpoint"] == "service/mapper/save"
    method, path, params = fake.requests[-1]
    assert method == "POST"
    assert json.loads(params["node"])["services"] == [SERVICE]
    assert path == "service/mapper/save"


async def test_empty_mapper_selection_does_not_map_everything(admin, fake):
    with pytest.raises(ValueError):
        await admin.map_services([])
    assert not fake.requests


async def test_satellite_frequency_hz_converted_to_native_khz(admin, fake):
    result = await admin.create_mux(
        NETWORK,
        {"symbolrate": 22000000, "delsys": "DVBS2", "polarisation": "H"},
        frequency_hz=11494000000,
    )
    assert result["response"]["uuid"]
    params = fake.requests[-1][2]
    assert json.loads(params["conf"])["frequency"] == 11494000


async def test_cable_frequency_hz_is_not_divided(admin, fake):
    fake.mux_schema["class"] = "dvb_mux_dvbc"
    await admin.create_mux(NETWORK, {"symbolrate": 6900000}, frequency_hz=450000000)
    assert json.loads(fake.requests[-1][2]["conf"])["frequency"] == 450000000


@pytest.mark.parametrize(
    "settings,frequency", [({"frequency": 11494000}, 11494000000), ({}, 11494000001), ({}, -1)]
)
async def test_ambiguous_or_invalid_mux_frequency_is_rejected(admin, fake, settings, frequency):
    with pytest.raises(ValueError):
        await admin.create_mux(NETWORK, settings, frequency_hz=frequency)
    assert all(method == "GET" for method, _, _ in fake.requests)


async def test_epg_recording_uses_event_string_and_default_profile(admin, fake):
    await admin.schedule_recording(123, comment="Test")
    params = fake.requests[-1][2]
    assert json.loads(params["entries"]) == [
        {"event_id": "123", "config_uuid": "", "comment": "Test"}
    ]


async def test_manual_timer_respects_utc_offset_across_dst(admin, fake):
    await admin.create_timer(
        CHANNEL, "DST", "2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00"
    )
    conf = json.loads(fake.requests[-1][2]["conf"])
    assert conf["stop"] - conf["start"] == 3600
    assert conf["start"] == int(datetime.fromisoformat("2026-10-25T02:30:00+02:00").timestamp())
    assert conf["config_name"] == ""


@pytest.mark.parametrize(
    "start,stop",
    [
        ("2026-10-25T02:30:00", "2026-10-25T03:00:00"),
        ("2026-10-25T04:00:00+01:00", "2026-10-25T03:00:00+01:00"),
    ],
)
async def test_ambiguous_or_reversed_timer_is_rejected_without_post(admin, fake, start, stop):
    with pytest.raises(ValueError):
        await admin.create_timer(CHANNEL, "test", start, stop)
    assert not fake.requests


@pytest.mark.parametrize(
    "action,path", [("stop", "stop"), ("cancel", "cancel"), ("remove_files", "remove")]
)
async def test_stop_cancel_and_media_deletion_use_distinct_endpoints(admin, fake, action, path):
    await admin.recording_action(RECORDING, action)
    assert fake.requests[-1][1] == "dvr/entry/" + path


async def test_generic_delete_rejects_recordings(admin, fake):
    with pytest.raises(ValueError, match="recording_action"):
        await admin.delete_node(RECORDING)
    assert all(method == "GET" for method, _, _ in fake.requests)


@pytest.mark.parametrize(
    "path",
    [
        "idnode/save",
        "service/mapper/save",
        "mpegts/network/scan",
        "hardware/satip/discover",
        "dvr/entry/remove",
        "http://evil.test/list",
        "idnode/delete",
    ],
)
async def test_read_api_cannot_bypass_write_controls(admin, fake, path):
    with pytest.raises(ValueError):
        await admin.api_read(path)
    assert not fake.requests


async def test_snapshot_excludes_readonly_and_password_fields(admin):
    snap = await admin.snapshot([TUNER, LNB])
    assert "active" not in snap["nodes"][0]["settings"]
    assert "pin" not in snap["nodes"][1]["settings"]
    assert snap["nodes"][0]["settings"]["satconf"] == "simple"


async def test_restore_validates_all_nodes_before_writing(admin, fake):
    snap = await admin.snapshot([TUNER, CHANNEL])
    snap["nodes"][1]["settings"]["not_a_field"] = 1
    with pytest.raises(ValueError):
        await admin.restore_snapshot(snap, dry_run=False)
    assert all(method == "GET" for method, _, _ in fake.requests)


async def test_restore_reports_partial_completion(admin, fake):
    snap = await admin.snapshot([TUNER, CHANNEL])
    fake.fail_write_for = CHANNEL
    result = await admin.restore_snapshot(snap, dry_run=False)
    assert result["complete"] is False
    assert len(result["results"]) == 1
    assert result["remaining_uuids"] == [CHANNEL]


async def test_list_muxes_uses_exact_network_filter_and_pagination(admin, fake):
    await admin.list_muxes(NETWORK, start=100, limit=250)
    params = fake.requests[-1][2]
    assert params["start"] == "100"
    assert params["limit"] == "250"
    assert json.loads(params["filter"])[0]["value"] == "^" + NETWORK + "$"


async def test_status_reports_endpoint_errors_without_hiding_success(admin, fake):
    original = fake.handler

    def handler(request):
        import httpx

        if request.url.path.endswith("status/inputs"):
            return httpx.Response(403)
        return original(request)

    admin.client.http._transport.handler = handler
    result = await admin.status()
    assert result["results"]["serverinfo"]["sw_version"] == "4.3-test"
    assert "HTTP 403" in result["results"]["status/inputs"]["error"]


def test_fractional_channel_numbers_use_split_integer_metadata():
    schema = {"props": [{"id": "number", "type": "s64", "intsplit": 1000000}]}
    validate_fields(schema, {"number": "36.1"})
