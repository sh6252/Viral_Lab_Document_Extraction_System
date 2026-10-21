"""Verify the frozen source inventory; optionally produce a source-only ZIP."""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "docs/release/source-files.json"


def submission_path(name: str) -> bool:
    path = PurePosixPath(name)
    return not (
        not path.parts or ".." in path.parts or path.is_absolute() or "\\" in name or ":" in name
        or any(ord(char) < 32 or char == '"' for char in name)
        or any(part in {".git", ".venv", "node_modules", "storage", "dist", "build", "__pycache__", "backups"} for part in path.parts)
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".dump", ".bak", ".log", ".xlsx", ".xls", ".zip", ".pyc", ".pem", ".key"}
        or (path.suffix.lower() == ".pdf" and name != "evaluation/fixtures/dev/smoke/synthetic_hebrew_commitment.pdf")
        or name.startswith(("demo/fixtures/", "evaluation/benchmark/", "evaluation/artifacts/", "evaluation/reports/", "evaluation/results/"))
    )


def git_output(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def verify_git_bytes(files: dict[str, bytes], *, index: bool = False) -> None:
    """Reject clean/filter transformations; optionally verify the entire staged tree.

    hash-object without -w is read-only. Comparing Git's filtered object IDs to
    unfiltered IDs detects both CRLF conversion and any configured clean filter.
    The manifest's SHA-256 remains a hash of actual bytes, never a guessed
    normalization of arbitrary binary input.
    """
    names = sorted(files)
    paths = ("\n".join(names) + "\n").encode("utf-8")
    raw = git_output("hash-object", "--no-filters", "--stdin-paths", data=paths).splitlines()
    clean = git_output("hash-object", "--stdin-paths", data=paths).splitlines()
    if len(raw) != len(names) or len(clean) != len(names):
        raise ValueError("GIT_OBJECT_COUNT_MISMATCH")
    for name, before, after in zip(names, raw, clean):
        if before != after:
            raise ValueError("GIT_CLEAN_CONTENT_MISMATCH: " + name)
    if not index:
        return
    entries = {}
    for entry in git_output("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, name_bytes = entry.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if mode != b"100644" or stage != b"0":
            raise ValueError("UNSAFE_GIT_INDEX_ENTRY")
        entries[name_bytes.decode("utf-8")] = oid
    if set(entries) != set(files):
        raise ValueError("GIT_INDEX_ALLOWLIST_MISMATCH")
    objects = BytesIO(git_output(
        "cat-file", "--batch", data=b"\n".join(entries[name] for name in names) + b"\n",
    ))
    for name in names:
        oid, kind, size = objects.readline().rstrip(b"\n").split()
        content = objects.read(int(size))
        if kind != b"blob" or oid != entries[name] or objects.read(1) != b"\n":
            raise ValueError("INVALID_GIT_BLOB")
        if content != files[name]:
            raise ValueError("GIT_INDEX_CONTENT_MISMATCH: " + name)
    if objects.read():
        raise ValueError("UNEXPECTED_GIT_OBJECT_DATA")


def verified_files(*, index: bool = False) -> dict[str, bytes]:
    manifest = json.loads((ROOT / INVENTORY).read_text(encoding="utf-8"))
    files = {}
    for name, expected in manifest["sha256"].items():
        if not submission_path(name):
            raise ValueError("UNSAFE_INVENTORY_PATH")
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("SOURCE_SYMLINK_REJECTED")
        content = path.read_bytes()
        if sha256(content).hexdigest() != expected:
            raise ValueError("SOURCE_INTEGRITY_MISMATCH: " + name)
        files[name] = content
    # The inventory does not hash itself. It is included in the output archive.
    files[INVENTORY] = (ROOT / INVENTORY).read_bytes()
    verify_git_bytes(files, index=index)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, help="New output path; existing files are never overwritten")
    parser.add_argument("--git-index", action="store_true", help="Also verify the exact staged file set and blob bytes")
    args = parser.parse_args()
    files = verified_files(index=args.git_index)
    if args.zip:
        with zipfile.ZipFile(args.zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(files.items()):
                entry = zipfile.ZipInfo(name, date_time=(2026, 10, 21, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, content)
        print("Source ZIP written; SHA-256=" + sha256(args.zip.read_bytes()).hexdigest())
    print(f"Verified {len(files)} release source files (including inventory); Git clean bytes match the inventory.")


if __name__ == "__main__":
    main()
