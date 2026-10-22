# Configuration

Use an explicit `--env-file /absolute/path/tvheadend.env` when the MCP client has
an unpredictable working directory. Otherwise `.env` in the current directory is
loaded. Existing process environment variables take precedence over file values.
An explicitly specified missing file is an error.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TVH_URL` | Required | Base URL, such as `http://tvheadend.example:9981` or `https://example.org/tvh`; omit `/api` |
| `TVH_USER` | Empty | Tvheadend username |
| `TVH_PASS` | Empty | Tvheadend password |
| `TVH_AUTH` | `digest` | `digest`, `basic` or `none`, matching the Tvheadend installation |
| `TVH_VERIFY_SSL` | `true` | Verify the Tvheadend HTTPS certificate |
| `TVH_TIMEOUT` | `30` | HTTP timeout in seconds |
| `TVH_TIMEZONE` | `Europe/Berlin` | Timezone for diagnostics; does not change Tvheadend's server timezone |
| `TVH_READ_ONLY` | `false` | Block writes; the provided `.env.example` explicitly sets `true` |

Do not put credentials in the URL. URL query strings and fragments are rejected.
The HTTP client does not follow redirects or inherit proxy settings from the
environment. Use the final Tvheadend base URL. Use HTTPS for Basic authentication
across untrusted networks; HTTP Digest does not encrypt the response contents.

For self-signed certificates, prefer configuring a trusted certificate over
disabling verification. Keep env files private, for example `chmod 600 .env`.

## Permissions

Tvheadend checks permissions on every API call. DVB management needs web-interface
access and administrator rights. DVR operations additionally need the appropriate
video-recorder permissions, including access to other users' entries if those
entries should be managed. A status request can succeed while an administrative
endpoint fails: inspect which `--check` result reports an error.

Use a dedicated account. `TVH_READ_ONLY` is a server-side safeguard for this MCP
process; it does not remove the Tvheadend account's underlying rights.

## CLI

```text
tvheadend-mcp [--env-file PATH] [--check]
              [--transport {stdio,streamable-http}]
              [--host {127.0.0.1,::1,localhost}] [--port PORT]
```

The default transport is stdio. Standard output is reserved for MCP protocol
messages, except the explicit `--check` command, which prints a JSON report and
exits. `--help` does not require a Tvheadend connection.

## Remote host

Start Streamable HTTP on the remote host's loopback interface:

```bash
uv run tvheadend-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

On the machine running the MCP client:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@your-server
```

Configure the client for `http://127.0.0.1:8000/mcp`. If port 8000 is occupied,
choose a different local port. The listener has no built-in authentication and
intentionally supports only loopback binding. Run it only on a trusted host.

For Docker, the documented image setup uses stdio. Publishing a Docker port does
not expose a service that binds to the container's loopback interface.

## Common problems

| Symptom | Check |
| --- | --- |
| `uv` not found by a desktop client | Use the full path from `command -v uv` and restart the client |
| HTTP 401/403 | Authentication mode, credentials and Tvheadend access-entry permissions |
| HTTP 404 | Base URL/proxy prefix and availability of the endpoint in this Tvheadend build |
| Writes blocked | Set `TVH_READ_ONLY=false` in the actual env file; check overriding process environment |
| Stale or missing tuner UUID | Refresh `tvh_hardware_tree`; topology changes can recreate child nodes |
| Missing channels/services | Follow all result pages and check native scan/mapper status |
| SAT>IP device not discovered | See the [SAT>IP section](dvb-workflow.md#satip-discovery) |
