# Security and trust model

## Reporting a vulnerability

Please use GitHub's **Security → Report a vulnerability** if private vulnerability
reporting is available for this repository. Otherwise, open an issue requesting
a private reporting channel without including exploit details, credentials or
private server information. Do not post configuration backups or env files.

The initial supported release line is 0.1.x. This is a beta project; reports should
include the package and Tvheadend versions and a minimal sanitized reproduction.

## Access boundary

This MCP server can act with all rights granted to its configured Tvheadend
account. An MCP client and its assistant can invoke administrative tools, change
reception settings, schedule recordings and delete objects or recording files.
Use a trusted client and host, a dedicated Tvheadend account and appropriate
client-side approval controls.

`TVH_READ_ONLY=true` blocks mutations in this process. Write tools support
`dry_run=true`; most writes execute by default, while snapshot restoration
previews by default. Tool annotations describe intent but are not an authorization
boundary. Tvheadend's own permissions are always still required.

Stdio inherits the permissions of the local process. Streamable HTTP binds only
to loopback and has no built-in authentication; other local users/processes may
be able to reach it. Use a trusted host and an SSH tunnel for remote use.

## Secrets and remote data

Keep `.env`, passwords, private keys, raw API dumps and backups outside version
control. `.gitignore`, Docker context exclusions and an explicit source-distribution
allowlist help prevent accidental inclusion. Package checks are an additional
guard, not a substitute for reviewing files before publication.

The HTTP client sanitizes request errors, does not follow redirects and ignores
environment proxy configuration. HTTPS certificate verification is enabled by
default. Digest authentication over HTTP does not encrypt programme/configuration
data. API results can contain private programme information, file paths and
subscription details; a connected assistant may forward them to its own service.

Treat EPG titles, service names and all other remote strings as data, never as
instructions to the assistant. Validate intended changes using the current
server metadata. Validation prevents unsupported field writes; it cannot verify
the physical wiring or that a proposed configuration suits the installation.

## Recovery limits

Selected-object snapshots omit password, read-only and write-once fields. They do
not recreate deleted objects or media. Restores are non-atomic and report partial
results. Back up the full Tvheadend configuration before structural changes.
Deleting a network can delete its muxes/services. `recording_action(remove_files)`
permanently deletes recording files; stop and cancel are separate operations.
