"""Source-only release packaging never exports local artifacts or overwrites ZIPs."""
from hashlib import sha256
import json
import subprocess
import sys
import zipfile

import pytest

from scripts import release_snapshot as release


@pytest.mark.parametrize("name", ["../secret", "C:/secret", "a\\secret", ".env", "frontend/.env.production",
    "storage/backup.json", "frontend/node_modules/test.js", "backup.dump", "records.xlsx",
    "evaluation/fixtures/holdout/private.pdf", "demo/fixtures/snapshot.json", ".git/config", "a\nb.py", '"quoted".py'])
def test_private_and_unsafe_paths_rejected(name):
    assert not release.submission_path(name)


def setup_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    release.git_output("config", "core.autocrlf", "true")
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\n*.pdf binary -text\n")
    (tmp_path / "README.md").write_bytes(b"Synthetic submission\n")
    manifest = tmp_path / release.INVENTORY
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"sha256": {"README.md": sha256(b"Synthetic submission\n").hexdigest()}}))


def test_tampered_source_is_rejected(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    (tmp_path / "README.md").write_bytes(b"Changed")
    with pytest.raises(ValueError, match="SOURCE_INTEGRITY_MISMATCH"):
        release.verified_files()


def test_inventory_cannot_export_private_file(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    (tmp_path / release.INVENTORY).write_text(json.dumps({"sha256": {".env": "irrelevant"}}))
    with pytest.raises(ValueError, match="UNSAFE_INVENTORY_PATH"):
        release.verified_files()


def test_archive_deterministic_and_excludes_unlisted_files_and_refuses_overwrite(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("SYNTHETIC_SECRET=do-not-export")
    for output in ("first.zip", "second.zip"):
        monkeypatch.setattr(sys, "argv", ["release_snapshot.py", "--zip", str(tmp_path / output)])
        release.main()
    assert (tmp_path / "first.zip").read_bytes() == (tmp_path / "second.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "first.zip") as archive:
        assert set(archive.namelist()) == {"README.md", release.INVENTORY}
    with pytest.raises(FileExistsError):
        release.main()


def test_environment_example_and_synthetic_fixture_are_allowed():
    assert release.submission_path(".env.example")
    assert release.submission_path("evaluation/fixtures/dev/smoke/synthetic_hebrew_commitment.pdf")


def test_crlf_hash_cannot_hide_git_clean_conversion(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    content = b"Synthetic submission\r\n"
    (tmp_path / "README.md").write_bytes(content)
    (tmp_path / release.INVENTORY).write_text(json.dumps({"sha256": {"README.md": sha256(content).hexdigest()}}))
    with pytest.raises(ValueError, match="GIT_CLEAN_CONTENT_MISMATCH: README.md"):
        release.verified_files()


def test_binary_bytes_survive_git_and_index(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    name = "evaluation/fixtures/dev/smoke/synthetic_hebrew_commitment.pdf"
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    content = b"%PDF-1.4\r\n\x00\xff\r\nSynthetic test only"
    path.write_bytes(content)
    (tmp_path / release.INVENTORY).write_text(json.dumps({"sha256": {name: sha256(content).hexdigest()}}))
    # The inventory is text too: use LF, independent of Windows write_text defaults.
    inventory = tmp_path / release.INVENTORY
    inventory.write_bytes(inventory.read_bytes().replace(b"\r\n", b"\n"))
    release.git_output("add", "--", name, release.INVENTORY)
    assert release.verified_files(index=True)[name] == content


def test_staged_blob_must_match_verified_working_bytes(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    release.git_output("add", "--", "README.md", release.INVENTORY)
    (tmp_path / "README.md").write_bytes(b"New synthetic content\n")
    (tmp_path / release.INVENTORY).write_bytes(json.dumps({"sha256": {"README.md": sha256(b"New synthetic content\n").hexdigest()}}).encode())
    with pytest.raises(ValueError, match="GIT_INDEX_CONTENT_MISMATCH"):
        release.verified_files(index=True)


def test_unlisted_staged_file_rejected(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    release.git_output("add", "--", "README.md", release.INVENTORY, ".gitattributes")
    with pytest.raises(ValueError, match="GIT_INDEX_ALLOWLIST_MISMATCH"):
        release.verified_files(index=True)


def test_custom_git_clean_filter_rejected(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    release.git_output("config", "filter.reject.clean", "echo transformed")
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\nREADME.md filter=reject\n")
    with pytest.raises(ValueError, match="GIT_CLEAN_CONTENT_MISMATCH"):
        release.verified_files()


def test_lf_checkout_overrides_autocrlf(tmp_path, monkeypatch):
    setup_inventory(tmp_path, monkeypatch)
    release.git_output("add", "--", ".gitattributes", "README.md")
    (tmp_path / "README.md").unlink()
    release.git_output("checkout-index", "--", "README.md")
    assert (tmp_path / "README.md").read_bytes() == b"Synthetic submission\n"


@pytest.mark.parametrize("suffix", ["pdf", "png", "zip", "xlsx"])
def test_repository_binary_policy_preserves_crlf(tmp_path, monkeypatch, suffix):
    policy = (release.ROOT / ".gitattributes").read_bytes()
    setup_inventory(tmp_path, monkeypatch)
    (tmp_path / ".gitattributes").write_bytes(policy)
    content = b"binary\r\n\x00\xff\r\n"
    name = "asset." + suffix
    (tmp_path / name).write_bytes(content)
    release.verify_git_bytes({name: content})
