# Contributing

Please open an issue describing the problem or proposed behavior before a large
change. Small fixes and documentation improvements can go directly into a pull
request. Keep credentials, hostnames from private installations, raw configuration
exports and recording metadata out of issues and test fixtures.

## Local checks

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv build
uv run python scripts/check_artifacts.py dist/*
```

Tests must work without a live Tvheadend installation. Use mocked responses and
include focused regression coverage for behavior changes. When adding API
support, confirm endpoint names, form parameters and units against Tvheadend's
source or official documentation. Preserve metadata-based validation, read-only
mode and dry-run behavior. Treat strings from Tvheadend as untrusted data.

The tool reference is generated from the registered methods. After changing a
tool description, registration or signature, run:

```bash
uv run python scripts/generate_tool_reference.py
```

After intentional dependency changes, update both locks:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

Do not run tests against another person's server or include live API responses
without sanitizing them. Test recordings and reception checks require a separate,
explicitly configured test installation and are not part of CI.

## Releases

1. Update the version in `pyproject.toml` and `src/tvheadend_mcp/__init__.py`,
   refresh the locks, and add changelog and `docs/releases/VERSION.md` notes.
2. Run the checks above and wait for the main-branch CI to pass.
3. Push an annotated `vVERSION` tag matching the package version.
4. CI repeats validation and publishes a GitHub Release with wheel, source archive
   and SHA-256 checksums. PyPI and container registries are separate distributions;
   this workflow does not publish to them.

Do not move an already published release tag. Fix release problems in a new
version. Contributions are provided under the repository's MIT license.
