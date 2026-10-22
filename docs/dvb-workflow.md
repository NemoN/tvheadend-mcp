# DVB configuration and channel setup

Start by reading the existing installation. Use real UUIDs and advertised field
options; the correct assignment node depends on the frontend and its topology.
Keep DVB-C and DVB-S/S2 networks separate.

1. Back up the complete Tvheadend configuration directory using your deployment's
   backup procedure. Avoid copying a changing directory without a consistent
   snapshot or a maintenance stop. Record the Tvheadend version and restore path.
2. Call `tvh_status` and `tvh_hardware_tree(recursive=true)`.
3. Read the relevant frontend with `tvh_get_node`. Inspect option fields with
   `tvh_get_field_options`. For satellite reception, follow the frontend's
   satellite configuration and child inputs down to LNB/switch/Unicable nodes.
4. Use `tvh_update_node(..., dry_run=true)` before changing reception topology.
   Refresh the tree after changing a configuration type: child UUIDs can change.
5. Inspect `tvh_network_types` and `tvh_list_networks`. Reuse the intended network,
   or create one with `tvh_create_network` using its advertised class/schema.
6. Use `tvh_assign_networks` on the node that actually owns the assignment field.
   DVB-C usually assigns networks on the frontend; DVB-S may assign them on a
   satellite input or on fields such as `network_a` or `network_aa`.
7. Read available scan tables, for example
   `tvh_api_read("dvb/scanfile/list", {"type":"dvbs","satpos":192})` for Astra
   19.2°E, or `{"type":"dvbc"}` for cable. Use lists offered by the running
   Tvheadend build, or create muxes from known tuning parameters and `tvh_mux_schema`.
8. Start `tvh_scan_networks` and monitor `tvh_list_networks` (`scanq_length`) and
   `tvh_list_muxes` (scan state and result). Scans are asynchronous and consume
   available tuner capacity.
9. Inspect `tvh_list_services` across every page. Select the wanted services and
   invoke `tvh_map_services` with explicit service UUIDs. Monitor
   `tvh_mapper_status`. Mapping is also asynchronous.
10. Inspect `tvh_list_channels`, set stable numbers, names, tags and EPG fields
    through node metadata, and verify EPG coverage with `tvh_search_epg`.

## Unicable/JESS and multiple reception paths

SCR IDs/user bands, frequencies, DiSEqC ports and satellite positions must come
from the real installation. Never infer an unused band's frequency solely from
the other tuners. Devices sharing one Unicable feed normally need distinct user
bands; confirm the LNB/multiswitch specification and actual wiring.

Tuners receiving the same satellite can share a network when its orbital position
and reception parameters match. Before consolidating duplicate networks, inspect
their muxes, services and channel assignments. Prefer retaining an existing
network and migrating assignments; deleting a network can cascade into muxes and
services. Selected-object snapshots cannot undo that deletion.

Map matching satellite and cable services onto one channel using
`tvh_assign_services(mode="add")` when you want alternative reception paths.
Confirm that the services carry the same programme and regional version.
Use priorities offered by the relevant nodes and recording profile to control
selection. Do not merge unrelated regional variants just because names match.

## Channels and EPG

For a curated list, choose free-to-air TV services, prefer HD where available,
and use explicit service selections instead of mapping every discovered service.
Keep regional variants intentional. Channel numbering and tags can be managed
through `tvh_update_node` and the channel-tag tools.

Check native EIT/EPG-related fields exposed by the networks, muxes, services and
channels. `tvh_api_read` can inspect allowlisted EPG-grabber configuration and
channel grids; its general-purpose access is read-only. The MCP server does not
provide an unrestricted API-write endpoint or install XMLTV grabbers. Global
grabber settings without a writable idnode need Tvheadend's own UI/configuration.

Allow time for over-the-air EPG collection, then check multiple channels and
future times. A service can tune successfully while providing little or no EPG.
EPG search alone does not prove reception quality. Validate actual viewing or a
short recording in a suitable client and inspect Tvheadend's input status/errors.

## SAT>IP discovery

`tvh_discover_satip` triggers Tvheadend's native discovery. SSDP discovery depends
on multicast networking on the **Tvheadend host**, even when the SAT>IP box's web
interface is reachable by unicast. The MCP process does not receive SSDP itself.

On a host with several network interfaces, check the selected SSDP route:

```bash
ip route get 239.255.255.250
```

The route and source address should use the interface on the SAT>IP server's
network. If needed, configure a persistent host route for
`239.255.255.250/32` through that interface using the host's existing network
manager. Route persistence and service restart procedures depend on the OS and
deployment. Tvheadend's HTTP listening address does not select the SSDP route.

Also check multicast isolation, VLAN boundaries, host firewall settings and the
SAT>IP server's announcements. If multicast cannot work, consult the Tvheadend
build's documented static SAT>IP device/startup configuration. Such host-level
settings are outside this MCP API. Do not assume that IP connectivity guarantees
automatic SSDP discovery.

## Units, pagination and recovery

- `create_mux.frequency_hz` uses integer Hz. DVB-S/S2 is converted to native kHz;
  DVB-C/T remains in Hz. Alternatively use `settings.frequency` in native units.
  Supplying both is rejected. Symbol rates use symbols per second.
- Lists default to 100 entries, EPG to 50, with a maximum of 1000. Follow `total`
  using `start`. Network diagnostics also have a bounded mux result.
- Assignment tools default to `add`. `replace` replaces the entire assignment
  list; `remove` removes only the supplied entries. A general node update replaces
  each supplied array, so read existing values first.
- `tvh_snapshot` saves selected writable fields. `tvh_restore_snapshot` previews
  by default and can report partial success. Neither replaces a complete backup.
