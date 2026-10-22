"""Reject private/unexpected files in release wheels and source distributions."""

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

SDIST_DIRS = {"src", "tests", "docs", "examples", "scripts", ".github"}
SDIST_FILES = {
    "README.md",
    "README.de.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",
    "requirements.lock",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "PKG-INFO",
}
FORBIDDEN_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "snapshots",
    "backups",
    "mac-app",
}


def check_name(name: str, *, wheel: bool) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive member: {name}")
    if any(part in FORBIDDEN_DIRS for part in path.parts):
        raise ValueError(f"Private/generated directory in artifact: {name}")
    for part in path.parts:
        if part.startswith(".env") and part != ".env.example":
            raise ValueError(f"Environment file in artifact: {name}")
        if part.endswith((".pem", ".key", ".pyc", ".pyo")) or part == ".DS_Store":
            raise ValueError(f"Private/generated file in artifact: {name}")
    top = path.parts[0]
    if wheel:
        if top != "tvheadend_mcp" and not top.endswith(".dist-info"):
            raise ValueError(f"Unexpected wheel member: {name}")
    elif top not in SDIST_DIRS and name not in SDIST_FILES:
        raise ValueError(f"Unexpected source-distribution member: {name}")


def check_artifact(path: Path) -> int:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
        for name in names:
            check_name(name, wheel=True)
        if not any(name.endswith("/licenses/LICENSE") for name in names):
            raise ValueError("Wheel is missing its license")
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            names = []
            roots = set()
            for item in archive:
                if item.isdir():
                    continue
                if not item.isfile():
                    raise ValueError(f"Non-regular source member: {item.name}")
                parts = PurePosixPath(item.name).parts
                if len(parts) < 2:
                    raise ValueError(f"Source member has no project root: {item.name}")
                roots.add(parts[0])
                name = str(PurePosixPath(*parts[1:]))
                check_name(name, wheel=False)
                names.append(name)
        if len(roots) != 1 or not {"LICENSE", "README.md", "pyproject.toml"} <= set(names):
            raise ValueError("Source distribution has an invalid root or missing metadata")
    else:
        raise ValueError(f"Unsupported artifact: {path.name}")
    if not names:
        raise ValueError(f"Empty artifact: {path.name}")
    return len(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.artifacts:
        count = check_artifact(path)
        print(f"{path.name}: {count} files; artifact allowlist passed.")


if __name__ == "__main__":
    main()
