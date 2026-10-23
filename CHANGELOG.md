# Changelog

## 0.2.0 — 2026-10-23

- 14 additional tools (53 total) for EPG administration, reception and maintenance.
- Native global EPG settings, installed module control, source assignment and OTA/internal triggers.
- Paginated EPG coverage reports with overlap-aware time coverage and description presence.
- Bounded MPEG-TS/media-packet probes, signal units and input-counter deltas.
- Explicit tuner isolation with idle/DVR checks, restoration and private recovery journals.
- Duplicate/HD-SD channel audits and reviewed merge/consolidation plans.
- Expiring plan IDs, stale-state checks, no object deletion and guarded rollback.

## 0.1.0 — 2026-10-22

Initial public release.

- 39 MCP tools for Tvheadend DVR, EPG and API-exposed DVB administration.
- Dynamic hardware, class, field and enum discovery, including satellite child
  nodes used for LNB, DiSEqC, rotor and Unicable/JESS settings.
- Network/mux management, native scanning, service mapping and channel assignment.
- DVR event recordings, offset-aware timers, autorec/timerec rules and profiles.
- Read-only mode, metadata validation, dry-run previews and selected-object snapshots.
- Official MCP Python SDK 2.x with stdio and loopback Streamable HTTP.
- English and German documentation, client examples, Dockerfile and CI.
