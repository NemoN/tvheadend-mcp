"""Upstream-shaped API responses; no live Tvheadend changes are made by tests."""

import copy
import json
from urllib.parse import parse_qs

import httpx
import pytest

from tvheadend_mcp.admin import TvheadendAdmin
from tvheadend_mcp.client import TvheadendClient
from tvheadend_mcp.config import Settings

TUNER = "1" * 32
SAT = "2" * 32
NETWORK = "3" * 32
SERVICE = "4" * 32
CHANNEL = "5" * 32
OTHER_SERVICE = "6" * 32
OTHER_NETWORK = "7" * 32
RECORDING = "8" * 32
LNB = "9" * 32


def prop(name, kind, value=None, **kwargs):
    return {"id": name, "type": kind, "value": value, **kwargs}


def node(uuid, cls, fields, **kwargs):
    return {"uuid": uuid, "id": uuid, "class": cls, "text": cls, "params": fields, **kwargs}


class FakeTVH:
    def __init__(self):
        self.requests = []
        self.fail_write_for = None
        self.nodes = {
            TUNER: node(
                TUNER,
                "linuxdvb_frontend_dvbs",
                [
                    prop("enabled", "bool", True),
                    prop(
                        "satconf",
                        "str",
                        "simple",
                        nosave=True,
                        enum=[
                            {"key": "simple", "val": "Universal LNB"},
                            {"key": "advanced", "val": "Advanced"},
                        ],
                    ),
                    prop("active", "bool", False, rdonly=True),
                ],
                leaf=0,
            ),
            SAT: node(
                SAT,
                "linuxdvb_satconf_ele",
                [
                    prop("networks", "str", [OTHER_NETWORK], list=1),
                    prop("lnb_type", "str", "Universal"),
                    prop("en50494_type", "str", ""),
                    prop("network_aa", "str", [OTHER_NETWORK], list=1),
                ],
                leaf=0,
            ),
            LNB: node(
                LNB,
                "linuxdvb_en50494",
                [
                    prop("id", "u16", 0),
                    prop("frequency", "u32", 1210),
                    prop("pin", "u16", 256, password=True),
                ],
                leaf=1,
            ),
            NETWORK: node(
                NETWORK,
                "dvb_network_dvbs",
                [
                    prop("networkname", "str", "Astra 19.2E"),
                    prop("enabled", "bool", True),
                    prop("orbital_pos", "str", "19.2E"),
                ],
            ),
            OTHER_NETWORK: node(
                OTHER_NETWORK,
                "dvb_network_dvbs",
                [
                    prop("networkname", "str", "Hotbird"),
                    prop("enabled", "bool", True),
                ],
            ),
            CHANNEL: node(
                CHANNEL,
                "channel",
                [
                    prop("name", "str", "Das Erste HD"),
                    prop("enabled", "bool", True),
                    prop("services", "str", [OTHER_SERVICE], list=1),
                ],
            ),
            SERVICE: node(SERVICE, "mpegts_service", [prop("enabled", "bool", True)]),
            OTHER_SERVICE: node(OTHER_SERVICE, "mpegts_service", [prop("enabled", "bool", True)]),
            RECORDING: node(RECORDING, "dvrentry", [prop("enabled", "bool", True)]),
        }
        self.network_schema = {
            "class": "dvb_network_dvbs",
            "props": [
                prop("networkname", "str"),
                prop("orbital_pos", "str"),
                prop("enabled", "bool"),
            ],
        }
        self.mux_schema = {
            "class": "dvb_mux_dvbs",
            "props": [
                prop("frequency", "u32"),
                prop("symbolrate", "u32"),
                prop("polarisation", "str", enum=[{"key": "H"}, {"key": "V"}]),
                prop("delsys", "str", enum=[{"key": "DVBS"}, {"key": "DVBS2"}]),
                prop("enabled", "int"),
                prop("pls_code", "u32"),
            ],
        }
        self.mapper = node(
            "a" * 32,
            "service_mapper",
            [
                prop("services", "str", [], list=1),
                prop("encrypted", "bool", False),
                prop("merge_same_name", "bool", True),
                prop("check_availability", "bool", False),
            ],
        )
        self.dvr_schema = {
            "class": "dvrentry",
            "props": [
                prop("channel", "str"),
                prop("disp_title", "str", nosave=True),
                prop("start", "time"),
                prop("stop", "time"),
                prop("config_name", "str"),
                prop("start_extra", "s64"),
                prop("stop_extra", "s64"),
                prop("comment", "str"),
            ],
        }

    def handler(self, request):
        params = (
            dict(request.url.params)
            if request.method == "GET"
            else {
                k: vals[0]
                for k, vals in parse_qs(request.content.decode(), keep_blank_values=True).items()
            }
        )
        path = request.url.path.split("/api/", 1)[1]
        self.requests.append((request.method, path, params))
        if path == "serverinfo":
            return httpx.Response(200, json={"sw_version": "4.3-test", "api_version": 19})
        if path == "idnode/load":
            saved = self.nodes.get(params.get("uuid"))
            return httpx.Response(200, json={"entries": [copy.deepcopy(saved)] if saved else []})
        if path == "idnode/save":
            patch = json.loads(params["node"])
            if patch["uuid"] == self.fail_write_for:
                return httpx.Response(403)
            current = self.nodes[patch["uuid"]]
            for field in current["params"]:
                if field["id"] in patch:
                    field["value"] = patch[field["id"]]
            return httpx.Response(200, json={})
        if path == "hardware/tree":
            keys = {"root": [TUNER], TUNER: [SAT], SAT: [LNB], LNB: []}
            return httpx.Response(
                200, json=[copy.deepcopy(self.nodes[u]) for u in keys[params["uuid"]]]
            )
        if path == "mpegts/network/builders":
            return httpx.Response(200, json={"entries": [self.network_schema]})
        if path == "mpegts/network/mux_class":
            return httpx.Response(200, json=self.mux_schema)
        if path == "dvr/entry/class":
            return httpx.Response(200, json=self.dvr_schema)
        if path == "service/mapper/load":
            return httpx.Response(200, json={"entries": [self.mapper]})
        if path == "dvr/entry/create_by_event":
            return httpx.Response(200, json={"uuid": [RECORDING]})
        if path.endswith("create"):
            return httpx.Response(200, json={"uuid": "b" * 32})
        if path.endswith(
            ("/grid", "grid_upcoming", "grid_failed", "grid_finished", "grid_removed")
        ):
            return httpx.Response(200, json={"entries": [], "total": 0})
        return httpx.Response(200, json={})


@pytest.fixture
def fake():
    return FakeTVH()


@pytest.fixture
async def admin(fake):
    client = TvheadendClient(
        Settings("http://tvh.test:9981/tvh", auth="none"),
        transport=httpx.MockTransport(fake.handler),
    )
    yield TvheadendAdmin(client)
    await client.close()
