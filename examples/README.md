# MCP client examples

- `mcp-client.json`: run a local checkout with its committed dependency lock.
- `mcp-client-git.json`: install from the versioned Git source with `uvx`.

These use the common `mcpServers` JSON format; check your client's configuration
format. Replace the absolute path placeholders with your own paths. Keep the env
file local. Desktop applications may require the full path to `uv` or `uvx`.

Start with `TVH_READ_ONLY=true`, inspect the hardware and schemas, then enable
writes when ready. Never paste real credentials into a public issue or example.
