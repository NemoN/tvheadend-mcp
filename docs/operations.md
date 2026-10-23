# EPG, reception and reviewed maintenance

These workflows are available in 0.2.0. They use Tvheadend's existing API and
streaming interface; no SSH access, ffmpeg executable or host-admin account is
required. Keep a complete configuration backup before structural maintenance.

## Configure and check EPG

1. Read `tvh_get_epg_config` for the actual fields, types and current values.
2. Preview `tvh_update_epg_config(settings=..., dry_run=true)`. This uses the
   dedicated global `epggrab/config/save` endpoint, not a guessed object UUID.
   Only specify fields advertised by the running build, such as its internal/OTA
   cron schedules, initial collection or EPG database save settings.
3. List installed grabbers with `tvh_list_epg_modules`. Inspect a module's fields
   with `tvh_get_node`, then preview/apply `tvh_update_epg_module` for enabled state,
   priority or other advertised settings.
4. Read `tvh_list_epg_sources`. For a channel's valid source IDs, call
   `tvh_get_field_options(channel_uuid, "epggrab")`. Source IDs can be strings
   such as a module/channel key; they are not necessarily UUIDs.
5. Use `tvh_assign_epg_sources` in `add`, `replace` or `remove` mode. The default
   `add` preserves existing sources. An explicitly empty `replace` clears them.
6. `tvh_trigger_epg_grab(kind="ota", delay_seconds=1)` queues over-the-air
   collection. `kind="internal"` reruns enabled internal grabbers immediately.
   Both support `dry_run`. Queuing does not mean collection has completed.
7. After collection, call `tvh_epg_coverage(channel_uuids=..., hours=24)`.

Coverage clips events to the requested window, unions overlapping intervals and
reports uncovered gaps. It also counts events with a summary/description. All
pages are read; a failed/truncated read produces an incomplete result rather than
a misleading percentage. The default selects enabled channels, explicit UUIDs
can include disabled channels. Limits: 500 channels, 1–168 hours, 10,000 events
per channel. A coverage result does not prove a programme is receivable.

Internal XMLTV grabbers must already be installed and configured on the
Tvheadend host. External XMLTV sockets still need their normal feed/import
process; this server does not install executables or upload XMLTV files. Running
internal grabbers does not import data into an external socket.

## Test reception

Select an enabled service UUID from `tvh_list_services` and preview:

```text
tvh_probe_reception(service_uuid=..., duration_seconds=10, dry_run=true)
```

After reviewing the preview, repeat with `dry_run=false`. The tool opens the
service stream with the `pass` profile and low subscription weight. It counts
188-byte MPEG-TS packets, transport-error flags, scrambling, continuity errors,
duplicate packets and loss of synchronization. The report identifies advertised
audio/video PIDs that actually appeared in the received packets, includes input
signal readings with their native scales and reports cumulative-counter changes
when at least two comparable samples exist. Counter resets are marked rather
than subtracted across unrelated inputs.

The tool saves no programme file and returns no raw media. It is a transport/media
packet check, not an audio/video decoder. A successful result needs sufficient
clean packets and an advertised media PID carrying packets. A quiet/off-air
service, slow tune or very short sample may not meet that requirement. Inspect
`seconds`, `stopped_by`, media components and errors; do not treat an API response
alone as successful reception. Counter measurements can be unavailable on idle
or unsupported devices.

Limits are 1–30 seconds and 18,800 bytes–64 MiB; the default cap is 16 MiB. Either
limit can end a test. A byte-limited test can have a shorter observation window.
The Tvheadend account requires advanced streaming rights and access to the `pass`
profile. Read-only mode blocks active probes even though streaming uses HTTP GET.

### Select a particular tuner

Tvheadend's HTTP service/mux stream does not provide a direct forced-input
parameter. Supplying `tuner_uuid` therefore requests **temporary isolation**:

- The UUID must identify an actual frontend from `tvh_hardware_tree`.
- The service must belong to a DVB network matching the frontend's delivery system;
  an IPTV stream cannot be used to claim successful reception by a DVB tuner.
- The server must be idle. Active inputs/subscriptions or active/imminent DVR
  entries block the test. Unknown recording padding also blocks isolation.
- Other enabled frontends are temporarily disabled; the selected frontend is
  enabled if necessary. All affected fields are checked against their metadata.
- A private, flushed recovery journal is written before the first change.
- The stream is tested with the selected frontend as the only enabled frontend.
  An additional subscription appearing during the test aborts observation.
- Completion, errors, timeouts and cancellation attempt to restore prior enabled
  states. Restoration conflicts are reported instead of overwritten.

Review the temporary changes and recovery snapshot before execution. Tests do
not alter network assignments or Unicable settings. If the selected frontend
cannot receive the service's network, the test fails; it does not silently choose
a different receiver. `tuner_verified` needs received packets and corresponding
input samples in addition to isolation.

The checks cannot reserve Tvheadend against every external client or newly
scheduled recording. Use a maintenance window for isolation. A process kill,
power loss or host crash can prevent restoration. Configure a persistent
`TVH_STATE_DIR` and check `tvh_reception_recoveries` after such an interruption.
Inspect the recorded UUIDs/current settings, then explicitly apply the journal's
`recovery_snapshot` using `tvh_restore_snapshot`. Retain the journal until you
have confirmed recovery; remove its local file manually afterwards. There is no
automatic startup restore.

## Audit and merge channels

Start with `tvh_audit_channels`. It reports mapping problems, duplicate numbers
and name-based duplicate/HD-SD candidates. Its normalization removes only an
explicit trailing HD/SD suffix and preserves regional words. A suggested HD target
is a name hint requiring programme verification.

Select the retained channel and explicit sources:

```text
tvh_plan_channel_merge(target_uuid=..., source_uuids=[...])
```

The plan keeps the target's name and number, unions services, tags and explicit
EPG sources, then disables the source channels. It does not delete them. Target
icons, timing and other scalar settings remain as configured. A name mismatch
blocks planning unless `allow_name_mismatch=true` is explicitly set after checking
the programmes. Source channels managed by a bouquet or referenced by DVR
entries/autorec/timerec rules block the plan; migrate those dependencies first.

## Consolidate DVB networks

The target network must already be enabled and scanned. Use:

```text
tvh_plan_network_consolidation(target_uuid=..., source_uuids=[...])
```

Only DVB-S/C/T networks of the same class can be consolidated. Satellite
positions must be known and identical. Each source service used by a channel
needs exactly one enabled target service with the same known **ONID, TSID and
SID**. Matching by programme name alone is insufficient. Missing or ambiguous
matches block execution; populate/scan and verify the target first.

The plan replaces source network assignments on tuner/satellite inputs, replaces
mapped source services on existing channels, then disables source networks. This
preserves channel UUIDs and their DVR/EPG associations. Bouquet-managed channel
changes are blocked. Old muxes/services stay stored under the disabled source
network, including unmapped services that were not migrated. The tool does not
move mux objects or automatically delete old networks.

## Review and apply a plan

A ready plan returns changes, prior values, warnings, a recovery snapshot and a
random `plan_id`. Review these before calling:

```text
tvh_apply_maintenance_plan(plan_id=..., dry_run=true)
tvh_apply_maintenance_plan(plan_id=..., dry_run=false)
```

IDs belong to the current process, expire after 15 minutes and are consumed when
execution begins. They cannot be fabricated by submitting an arbitrary JSON
patch. Plans are limited to 100 changed objects; split larger work into smaller
steps. Inventory and affected fields are reread before execution. A changed
inventory requires a fresh plan. Active inputs/subscriptions and imminent DVR
work block execution.

Changes are sequential and verified by readback. On a partial failure, rollback
attempts to restore prior fields only where the current fields still match this
operation's writes. Concurrent external edits are reported as conflicts.
Cancellation also attempts rollback. Inspect `complete`, `rollback_complete` and
every rollback result, and retain the reviewed recovery snapshot.

Tvheadend provides no cross-client transaction lock. A process/host crash can
interrupt maintenance and rollback. The plan snapshot is not a full backup and
does not restore deleted objects/media. No cleanup tool in this workflow deletes
objects or recordings automatically.

## Endpoint references

Verified against the official API documentation and upstream implementation:
[EPG grabbers](https://docs.tvheadend.org/documentation/development/json-api/api-description/epggrab),
[input statistics](https://docs.tvheadend.org/documentation/development/json-api/api-description/status),
[MPEGts](https://docs.tvheadend.org/documentation/development/json-api/api-description/mpegts),
and [HTTP streaming implementation](https://github.com/tvheadend/tvheadend/blob/master/src/webui/webui.c).
