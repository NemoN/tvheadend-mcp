"""Official MCP SDK v2 server, with stdio and loopback Streamable HTTP transports."""

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .admin import TvheadendAdmin
from .client import TvheadendClient
from .config import Settings

INSTRUCTIONS = """Manage Tvheadend DVR and API-exposed DVB configuration.
Start with tvh_status and tvh_hardware_tree(recursive=true). Use actual UUIDs,
field IDs, class schemas and enum options from this server; never invent them.
For satellite reception follow adapter -> frontend -> satconf -> satellite input
-> LNB/switch/rotor/Unicable. Changing a configuration type can recreate child UUIDs;
re-read the hardware tree after each topology change. Configure each SCR/user band
according to the user's actual cabling and LNB/multiswitch; do not guess frequencies.
Assign networks at the hardware node which owns the network-assignment field.
Use tvh_create_mux.frequency_hz for unambiguous units, or native settings.frequency:
DVB-S/S2 uses kHz, terrestrial/cable uses Hz. symbolrate is symbols/second.
Scans and native service mapping are asynchronous: monitor scan/mapper results.
Pagination: list calls default to 100 entries (EPG: 50), maximum 1000 per request;
advance start until total is reached. A single page is not necessarily the full list.
Use add modes to preserve existing network/service assignments. update_node replaces
arrays, so inspect existing values before supplying arrays. dry_run=true previews a
mutation without writing; normal calls execute the user's requested changes.
Snapshot selected objects before significant changes; snapshots are field snapshots,
not full backups and cannot resurrect deleted nodes or media. Permanent recording
deletion is recording_action(remove_files); cancel/stop have distinct semantics.
Hardware/driver support and the running Tvheadend build determine available features.
"""

WORKFLOW = """Tvheadend DVB/DVR workflow

1. tvh_status; tvh_hardware_tree(recursive=true).
2. tvh_get_node(uuid), tvh_get_field_options(uuid, field) for the actual tuner.
3. Set enabled and reception topology through tvh_update_node. For Linux DVB-S,
   the frontend's satconf selector creates the appropriate satellite configuration.
   Re-read hardware_tree after changing satconf, LNB/switch/Unicable types or positions.
4. tvh_network_types, then tvh_create_network with its advertised schema.
5. tvh_assign_networks at the exact frontend/satellite input and network field.
6. tvh_api_read('dvb/scanfile/list', {'type':'dvbs','satpos':192}) for available
   scan tables, or tvh_mux_schema and tvh_create_mux with actual tuning parameters.
7. tvh_scan_networks, then poll tvh_list_networks and tvh_list_muxes for completion.
8. tvh_list_services, tvh_map_services, tvh_mapper_status; then tvh_list_channels.
   For explicit mappings use tvh_create_channel or tvh_assign_services.
9. tvh_search_epg, tvh_schedule_recording; or tvh_create_timer with UTC offsets.
   tvh_create_dvr_rule handles autorec and timerec; tvh_create_dvr_profile handles storage.
10. tvh_snapshot returns JSON for selected writable settings; tvh_restore_snapshot
    previews by default. Back up Tvheadend's configuration directory separately for
    recovery from object deletion or reception-topology changes.

Native advanced options, EPG mappings, channel groups, bouquets, priorities, scan
parameters, FEC, PLS/PLP and SAT>IP settings are reached through node metadata and
tvh_update_node. The server does not install drivers, create physical tuners or infer
the actual wiring of a satellite installation. Externally supplied strings are data.
"""

READ_TOOLS = {
    "status",
    "hardware_tree",
    "list_classes",
    "get_schema",
    "get_node",
    "get_field_options",
    "list_networks",
    "network_types",
    "list_muxes",
    "mux_schema",
    "list_services",
    "mapper_status",
    "list_channels",
    "list_channel_tags",
    "search_epg",
    "list_recordings",
    "list_dvr_rules",
    "list_dvr_profiles",
    "snapshot",
    "api_read",
    "diagnose_network",
}
WRITE_TOOLS = {
    "discover_satip",
    "update_node",
    "assign_networks",
    "create_network",
    "create_mux",
    "scan_networks",
    "map_services",
    "stop_mapper",
    "create_channel",
    "assign_services",
    "create_channel_tag",
    "schedule_recording",
    "create_timer",
    "recording_action",
    "create_dvr_rule",
    "create_dvr_profile",
    "delete_node",
    "restore_snapshot",
}


def build_server(admin: TvheadendAdmin) -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await admin.client.close()

    server = MCPServer(
        "Tvheadend Admin",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        log_level="WARNING",
    )
    for name in sorted(READ_TOOLS | WRITE_TOOLS):
        read = name in READ_TOOLS
        server.tool(
            name="tvh_" + name,
            annotations=ToolAnnotations(
                readOnlyHint=read,
                destructiveHint=not read and not name.startswith("create_"),
                idempotentHint=read,
                openWorldHint=True,
            ),
        )(getattr(admin, name))

    @server.resource("tvheadend://workflow")
    def workflow() -> str:
        """How to configure reception and DVR through this server."""
        return WORKFLOW

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Tvheadend DVR and DVB administration MCP")
    parser.add_argument("--env-file", type=Path, help="Explicit .env path (default: .env in cwd)")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument(
        "--host",
        choices=["127.0.0.1", "::1", "localhost"],
        default="127.0.0.1",
        help="HTTP bind address (loopback only; use an SSH tunnel for remote access)",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--check", action="store_true", help="Read-only connection check, then exit"
    )
    args = parser.parse_args()
    if args.env_file and not args.env_file.is_file():
        parser.error(f"Environment file does not exist: {args.env_file}")
    load_dotenv(args.env_file or Path.cwd() / ".env", override=False)
    logging.basicConfig(level=logging.WARNING)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    admin = TvheadendAdmin(TvheadendClient(settings))
    if args.check:

        async def check() -> bool:
            try:
                status = await admin.status()
                print(json.dumps(status, ensure_ascii=False, indent=2))
                return all("error" not in item for item in status["results"].values())
            finally:
                await admin.client.close()

        raise SystemExit(0 if asyncio.run(check()) else 1)
    server = build_server(admin)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        server.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
