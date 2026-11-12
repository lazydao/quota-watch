"""Set the Quota Watch version in every file that carries it.

Usage: python scripts/set_version.py 0.1.9 [--root PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")

# (file, pattern with the version in group 2, expected number of matches)
TARGETS = (
    ("pyproject.toml", r'(?m)^(version[ \t]*=[ \t]*")[^"]+(")', 1),
    ("src/quota_watch/__init__.py", r'(?m)^(__version__[ \t]*=[ \t]*")[^"]+(")', 1),
    ("windows/QuotaWatch.Tray/QuotaWatch.Tray.csproj", r"(<Version>)[^<]+(</Version>)", 1),
    ("README.md", r'(?m)^(\$?version[ \t]*=[ \t]*"v)[^"]+(")', 2),
)


def set_version(root: Path, version: str) -> list[str]:
    """Rewrite the version and return the files that changed."""
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Version must look like 1.2.3, got {version!r}.")

    updates: list[tuple[Path, str]] = []
    changed: list[str] = []
    for name, pattern, expected in TARGETS:
        path = root / name
        # newline="" keeps each file's own line endings.
        with path.open(encoding="utf-8", newline="") as stream:
            original = stream.read()
        updated, count = re.subn(pattern, lambda match: match[1] + version + match[2], original)
        if count != expected:
            raise ValueError(f"Expected {expected} version field(s) in {name}, found {count}.")
        if updated != original:
            updates.append((path, updated))
            changed.append(name)

    # Write only after every file validated, so a failure changes nothing.
    for path, updated in updates:
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="New version, for example 0.1.9 (a leading v is accepted).")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        changed = set_version(args.root, args.version.removeprefix("v"))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Updated: " + ", ".join(changed) if changed else "Version already set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
