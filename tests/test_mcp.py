"""Protocol-level checks through the official SDK and a real stdio child process."""

import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tvheadend_mcp.server import READ_TOOLS, WRITE_TOOLS, build_server

from .conftest import TUNER

PROJECT = Path(__file__).resolve().parents[1]


async def test_mcp_tool_schemas_calls_resources_and_errors(admin, fake):
    async with Client(build_server(admin)) as client:
        result = await client.list_tools()
        indexed = {tool.name: tool for tool in result.tools}
        assert set(indexed) == {"tvh_" + name for name in READ_TOOLS | WRITE_TOOLS}
        assert indexed["tvh_status"].annotations.read_only_hint is True
        assert indexed["tvh_update_node"].annotations.read_only_hint is False
        assert indexed["tvh_update_node"].input_schema["required"] == ["uuid", "settings"]
        status = await client.call_tool("tvh_status", {})
        assert not status.is_error
        assert status.structured_content["results"]["serverinfo"]["sw_version"] == "4.3-test"
        preview = await client.call_tool(
            "tvh_update_node",
            {
                "uuid": TUNER,
                "settings": {"satconf": "advanced"},
                "dry_run": True,
            },
        )
        assert not preview.is_error
        assert preview.structured_content["dry_run"] is True
        error = await client.call_tool(
            "tvh_update_node",
            {
                "uuid": TUNER,
                "settings": {"active": True},
            },
        )
        assert error.is_error
        assert all(method == "GET" for method, _, _ in fake.requests)
        resource = await client.read_resource("tvheadend://workflow")
        assert "tvh_assign_networks" in resource.contents[0].text


async def test_stdio_process_initializes_without_touching_tvheadend(tmp_path):
    # An unavailable upstream is intentional: MCP initialization/listing is offline.
    env_file = tmp_path / "tvh.env"
    env_file.write_text("TVH_URL=http://127.0.0.1:1\nTVH_AUTH=none\nTVH_TIMEOUT=0.1\n")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tvheadend_mcp", "--env-file", str(env_file)],
        cwd=str(PROJECT),
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 39
        status = await client.call_tool("tvh_status", {})
        assert not status.is_error
        assert "Connection failed" in status.structured_content["results"]["serverinfo"]["error"]
