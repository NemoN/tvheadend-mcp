# Tvheadend MCP

[![CI](https://github.com/NemoN/tvheadend-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/NemoN/tvheadend-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tvheadend MCP on AI Agents Listing](https://aiagentslisting.com/tvheadend-mcp/badge.svg?claim=8a56516f639f428420e8e74afe08185c)](https://aiagentslisting.com/mcp/tvheadend-mcp)

**English** · [Deutsch](README.de.md)

An MCP server for **Tvheadend DVR, EPG and API-exposed DVB configuration**.
Its 53 tools let an MCP client inspect and configure tuners, networks, muxes,
services and channels, including satellite reception with DiSEqC and Unicable/JESS.

The server reads the running Tvheadend instance's classes, field metadata and
options instead of maintaining a fixed list of supported tuner models. It uses
the official MCP Python SDK 2.x and supports stdio and loopback Streamable HTTP.
No AI service or API key is required by this server.

## Features

| Area | Capabilities |
| --- | --- |
| Hardware | Adapter/frontend tree, enabled state, priorities, SAT>IP discovery |
| Satellite | Satconfig, LNB, DiSEqC, rotor and Unicable/JESS fields exposed by the device's child nodes |
| Networks and muxes | Create, inspect, update, assign, scan and delete; use the connected server's schemas |
| Channels | Inspect services, run the native mapper, assign multiple services, manage channel tags and EPG fields |
| DVR and EPG | Search and measure EPG, configure grabbers/schedules, map sources, trigger collection, manage recordings and rules |
| Diagnostics | Server version, signal values, bounded MPEG-TS reception probes, input error deltas and EPG coverage |
| Maintenance | Duplicate/HD-SD audit, reviewed channel merges and DVB network consolidation without object deletion |
| Change control | Field validation, previews, read-only mode, snapshots, expiring plans and guarded rollback |

“API-exposed” is an important boundary: drivers, firmware, physical wiring and
operating-system networking are outside this server. Some builds omit classes or
endpoints. Development targets Tvheadend 4.3; older versions depend on the API they
provide. Missing endpoints produce errors rather than simulated success.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use Python
3.11 or newer. Clone the repository for a reproducible setup:

```bash
git clone https://github.com/NemoN/tvheadend-mcp.git
cd tvheadend-mcp
git checkout v0.2.0
uv sync --frozen
cp .env.example .env
```

Edit `.env` locally with the URL and credentials of your own Tvheadend instance.
The example enables `TVH_READ_ONLY=true` for initial exploration. Change it to
`false` when you want tools to apply changes. The code default, if this variable
is omitted, is `false`.

```dotenv
TVH_URL=http://tvheadend.example:9981
TVH_USER=mcp
TVH_PASS=replace-locally
TVH_AUTH=digest
TVH_VERIFY_SSL=true
TVH_TIMEOUT=30
TVH_TIMEZONE=Europe/Berlin
TVH_READ_ONLY=true
```

The Tvheadend account needs web-interface access and administrator privileges for
DVB administration, plus the appropriate video-recorder permissions for DVR work.
Tvheadend continues to enforce these permissions. See [configuration](docs/configuration.md).

Check connectivity without changing the configuration:

```bash
uv run tvheadend-mcp --check
```

Exit code 0 means all three status endpoints answered successfully; 1 indicates
an API, connection or permission error. Starting without `--check` runs an MCP
server and waits for a client.

### Connect an MCP client

For clients using the common `mcpServers` JSON format, replace both absolute paths:

```json
{
  "mcpServers": {
    "tvheadend": {
      "command": "uv",
      "args": [
        "run", "--directory", "/absolute/path/tvheadend-mcp",
        "--frozen", "tvheadend-mcp",
        "--env-file", "/absolute/path/tvheadend.env"
      ]
    }
  }
}
```

The env file can be the checkout's `.env` or another private file. If a desktop
client cannot find `uv`, use its full executable path. Clients with a different
configuration format use the same command and arguments in their MCP settings.
See [examples](examples/).

Alternatively, install directly from the versioned Git source without a checkout:

```bash
uvx --from git+https://github.com/NemoN/tvheadend-mcp.git@v0.2.0 \
  tvheadend-mcp --env-file /absolute/path/tvheadend.env
```

This installation uses Git source; a PyPI package or container-registry image is
not required. For a fully locked environment, use the checkout and `uv sync --frozen`.

### Streamable HTTP

```bash
uv run tvheadend-mcp --transport streamable-http --port 8000
```

Connect to `http://127.0.0.1:8000/mcp`. The HTTP listener accepts only loopback
addresses and has no built-in authentication. For a remote host, use an SSH
tunnel; see [configuration](docs/configuration.md#remote-host).

## Example requests

> Inspect my DVB-C and DVB-S/S2 hardware and networks. Show missing assignments
> and use the actual tuner schemas and options before changing anything.

> Find free-to-air TV services, show the proposed channel list and map the
> services I choose. Prefer HD services and preserve existing channel numbers.

> Find the next showing of a programme in my EPG and schedule a recording using
> my default recording profile.

Follow the [DVB setup guide](docs/dvb-workflow.md) for tuners, Unicable, SAT>IP,
networks, scans, channel mapping and EPG. The same basic workflow is available to
MCP clients as the resource `tvheadend://workflow`.

## New in 0.2.0

See the [EPG, reception and maintenance guide](docs/operations.md) for complete
workflows and recovery limits. The new tools can measure EPG time coverage and
description presence, manage native grabbers, test received media packets, and
prepare channel/network cleanup plans that preserve retained channel UUIDs.

A tuner-specific probe temporarily isolates the selected frontend, requires an
idle server and no imminent DVR, and records a recovery journal before changing
settings. Probes and maintenance execution **preview by default**.

## Tool reference

All tools start with `tvh_`. The [complete reference](docs/tools.md) documents
their arguments and descriptions.

| Group | Tools, without the `tvh_` prefix |
| --- | --- |
| Status and hardware | `status`, `hardware_tree`, `discover_satip`, `diagnose_network` |
| Schemas and nodes | `list_classes`, `get_schema`, `get_node`, `get_field_options`, `update_node`, `delete_node`, `api_read` |
| Networks | `network_types`, `list_networks`, `create_network`, `assign_networks`, `scan_networks` |
| Muxes | `list_muxes`, `mux_schema`, `create_mux` |
| Services and channels | `list_services`, `map_services`, `mapper_status`, `stop_mapper`, `list_channels`, `create_channel`, `assign_services`, `list_channel_tags`, `create_channel_tag` |
| DVR and EPG | `search_epg`, `list_recordings`, `schedule_recording`, `create_timer`, `recording_action`, `list_dvr_rules`, `create_dvr_rule`, `list_dvr_profiles`, `create_dvr_profile` |
| EPG administration | `get_epg_config`, `update_epg_config`, `list_epg_modules`, `update_epg_module`, `list_epg_sources`, `assign_epg_sources`, `trigger_epg_grab`, `epg_coverage` |
| Reception | `probe_reception`, `reception_recoveries` |
| Maintenance | `audit_channels`, `plan_channel_merge`, `plan_network_consolidation`, `apply_maintenance_plan` |
| Snapshots | `snapshot`, `restore_snapshot` |

List calls default to 100 entries, EPG searches to 50; the maximum is 1000 per
request. Advance `start` until `total` is reached. `assign_networks` and
`assign_services` default to adding entries; a general `update_node` replaces
each supplied array in full.

`create_mux.frequency_hz` uses Hz and converts DVB-S/S2 to Tvheadend's native kHz.
Native `settings.frequency` is also supported, but cannot be supplied together
with `frequency_hz`. Symbol rates use symbols per second.

## Changes and recovery

Write tools accept `dry_run=true` to preview their endpoint and parameters.
Normally they execute immediately; `restore_snapshot`, `probe_reception` and
`apply_maintenance_plan` preview by default. Maintenance planning never writes to
Tvheadend; applying a plan requires its unexpired ID from the same process.
`TVH_READ_ONLY=true` blocks writes while allowing reads and previews.

Snapshots contain selected writable settings, excluding password, read-only and
write-once fields. They are **not complete backups** and cannot recreate deleted
objects, restore media or recover old hardware child UUIDs. Restoration is
non-atomic and reports partial results. Back up the complete Tvheadend
configuration directory before structural changes.

Deleting a network can also delete its muxes and services. DVR `stop`, `cancel`
and `remove_files` have different meanings: **`remove_files` permanently deletes
recording files**. Consult the [security and trust model](SECURITY.md).

## Docker

```bash
docker build -t tvheadend-mcp .
docker run --rm -i --env-file .env tvheadend-mcp
```

Use `-i` without `-t` for stdio. `TVH_URL` must resolve and be reachable from the
container; `localhost` inside the container refers to that container. The image
runs as an unprivileged user and installs hashed runtime dependencies from
`requirements.lock`. No prebuilt container image is currently published. For tuner-isolation probes,
mount a persistent state folder writable by UID 65534 and set `TVH_STATE_DIR` to
that folder inside the container (for example `/state`).

## Development

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv build
uv run python scripts/check_artifacts.py dist/*
```

Tests use mocked Tvheadend HTTP responses and a real MCP stdio subprocess; they
do not require access to a live tuner. CI runs them on Python 3.11–3.14, checks
formatting and package contents, and builds the Docker image. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Project information

MIT licensed. Independent project; not affiliated with Tvheadend or a tuner
manufacturer. Report reproducible problems through
[GitHub Issues](https://github.com/NemoN/tvheadend-mcp/issues) without credentials
or configuration backups. Please use the private reporting route described in
[SECURITY.md](SECURITY.md) for vulnerabilities.

Implementation references: [Tvheadend JSON API](https://docs.tvheadend.org/documentation/development/json-api),
[Tvheadend source](https://github.com/tvheadend/tvheadend/tree/master/src/api),
and the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
