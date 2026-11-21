from __future__ import annotations

import os
from pathlib import Path

import pytest

from bip.config import load_settings
from bip.core.exceptions import StorageLimitError, StoragePathError
from bip.storage.disk_guard import DiskGuard, DiskUsage, directory_size, guard_from_settings
from bip.storage.paths import Layer, StorageRoot

GIB = 1024**3


def usage(total: int, free: int):
    def fn(path: Path) -> DiskUsage:
        del path
        return DiskUsage(total=total, used=total - free, free=free)

    return fn


# ---------- StorageRoot ----------


def test_paths_stay_inside_root(tmp_path: Path) -> None:
    root = StorageRoot(tmp_path / "data")
    p = root.layer_path(Layer.BRONZE, "chain=ethereum", "dataset=blocks", "part-1.parquet")
    assert (
        p == (tmp_path / "data" / "bronze/chain=ethereum/dataset=blocks/part-1.parquet").resolve()
    )


@pytest.mark.parametrize(
    "parts",
    [("..", "etc"), ("a/../../etc",), ("/etc/passwd",), ("",), ("a\x00b",), ("\\server\\share",)],
)
def test_traversal_and_absolute_components_rejected(tmp_path: Path, parts: tuple[str, ...]) -> None:
    with pytest.raises(StoragePathError):
        StorageRoot(tmp_path).path(*parts)


def test_root_itself_is_not_a_target(tmp_path: Path) -> None:
    with pytest.raises(StoragePathError):
        StorageRoot(tmp_path).path(".")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    root_dir = tmp_path / "root"
    outside = tmp_path / "outside"
    root_dir.mkdir()
    outside.mkdir()
    (root_dir / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoragePathError, match="outside"):
        StorageRoot(root_dir).path("link", "file.parquet")


def test_missing_root_is_reported_not_created(tmp_path: Path) -> None:
    root = StorageRoot(tmp_path / "absent")
    assert not root.exists()
    with pytest.raises(StoragePathError, match="does not exist"):
        root.require_exists()
    with pytest.raises(StoragePathError, match="does not exist"):
        root.create_exclusive("bronze", "x.bin")
    assert not (tmp_path / "absent").exists()


def test_create_exclusive_never_overwrites(tmp_path: Path) -> None:
    root = StorageRoot(tmp_path)
    with root.create_exclusive("bronze", "a.bin") as fh:
        fh.write(b"original")
    with pytest.raises(StoragePathError, match="overwrite"):
        root.create_exclusive("bronze", "a.bin")
    assert (tmp_path / "bronze" / "a.bin").read_bytes() == b"original"


# ---------- DiskGuard ----------


def test_allows_write_within_all_limits(tmp_path: Path) -> None:
    guard = DiskGuard(
        tmp_path,
        max_data_bytes=1 * GIB,
        min_free_bytes=1 * GIB,
        min_free_ratio=0.05,
        disk_usage=usage(100 * GIB, 50 * GIB),
    )
    result = guard.check(10 * 1024**2)
    assert result.allowed
    assert result.reasons == ()
    assert guard.require(1024).free_bytes == 50 * GIB


def test_refuses_when_bip_quota_exceeded(tmp_path: Path) -> None:
    (tmp_path / "f.bin").write_bytes(b"x" * 900)
    guard = DiskGuard(
        tmp_path,
        max_data_bytes=1000,
        min_free_bytes=0,
        min_free_ratio=0.0,
        disk_usage=usage(100 * GIB, 50 * GIB),
    )
    assert guard.check(100).allowed
    result = guard.check(101)
    assert not result.allowed
    assert "limit" in result.reasons[0]
    with pytest.raises(StorageLimitError, match="write refused"):
        guard.require(101)


def test_refuses_when_free_space_floor_hit(tmp_path: Path) -> None:
    # Mirrors the owner's VPS: 145 GB disk, ~94% used -> ~8.7 GB free.
    guard = DiskGuard(
        tmp_path,
        max_data_bytes=100 * GIB,
        min_free_bytes=5 * GIB,
        min_free_ratio=0.05,
        disk_usage=usage(145 * GIB, int(8.7 * GIB)),
    )
    assert guard.check(1 * GIB).allowed
    result = guard.check(4 * GIB)
    assert not result.allowed
    assert any("floor" in r for r in result.reasons)
    assert any("%" in r for r in result.reasons)


def test_missing_root_measures_zero_on_existing_parent(tmp_path: Path) -> None:
    seen: list[Path] = []

    def fn(path: Path) -> DiskUsage:
        seen.append(path)
        return DiskUsage(10 * GIB, 0, 10 * GIB)

    guard = DiskGuard(
        tmp_path / "not" / "yet",
        max_data_bytes=GIB,
        min_free_bytes=0,
        min_free_ratio=0.0,
        disk_usage=fn,
    )
    st = guard.status()
    assert st.bip_used_bytes == 0
    assert seen == [tmp_path.resolve()]
    assert not (tmp_path / "not").exists()


def test_fails_safe_when_usage_unmeasurable(tmp_path: Path) -> None:
    def broken(path: Path) -> int:
        raise PermissionError(13, "Permission denied")

    guard = DiskGuard(
        tmp_path,
        max_data_bytes=GIB,
        min_free_bytes=0,
        min_free_ratio=0.0,
        disk_usage=usage(GIB * 10, GIB * 5),
        measure=broken,
    )
    with pytest.raises(StorageLimitError, match="refusing writes"):
        guard.check(1)


def test_directory_size_ignores_symlinks(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_bytes(b"12345")
    big = tmp_path.parent / f"{tmp_path.name}-outside.bin"
    big.write_bytes(b"x" * 10_000)
    try:
        os.symlink(big, tmp_path / "a" / "link")
        assert directory_size(tmp_path) == 5
    finally:
        big.unlink()
    assert directory_size(tmp_path / "missing") == 0


def test_invalid_guard_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        DiskGuard(tmp_path, max_data_bytes=0, min_free_bytes=0, min_free_ratio=0.0)
    guard = DiskGuard(tmp_path, max_data_bytes=1, min_free_bytes=0, min_free_ratio=0.0)
    with pytest.raises(ValueError, match="planned_bytes"):
        guard.check(-1)


def test_guard_from_settings_uses_configured_limits(tmp_path: Path) -> None:
    settings = load_settings(data_root=tmp_path, max_data_bytes="1MiB", min_free_bytes=0)
    guard = guard_from_settings(settings)
    assert guard.status().max_data_bytes == 1024**2


def test_parent_that_is_a_file_is_a_storage_error(tmp_path: Path) -> None:
    (tmp_path / "bronze").write_bytes(b"not a directory")
    with pytest.raises(StoragePathError, match="not a directory"):
        StorageRoot(tmp_path).create_exclusive("bronze", "x.bin")
