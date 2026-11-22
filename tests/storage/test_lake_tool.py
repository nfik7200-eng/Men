from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bip.storage.lake.manifest import Lineage
from bip.storage.lake.writer import LakeWriter
from bip.storage.paths import StorageRoot
from bip.tools.lake import main
from tests.storage.crash_child import PARTITION
from tests.storage.lake_fakes import BLOCKS, JsonLinesCodec, roomy_guard, rows


@pytest.fixture
def root(tmp_path: Path) -> StorageRoot:
    r = StorageRoot(tmp_path / "data")
    r.create()
    w = LakeWriter(r, codec=JsonLinesCodec(), guard=roomy_guard(r))
    w.write(BLOCKS, PARTITION, rows([1, 2]), lineage=Lineage(job="t"))
    return r


def test_verify_ok_and_stats(root: StorageRoot, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--data-root", str(root.root)]
    code = main(
        [*base, "verify", "--layer", "bronze", "--chain", "ethereum", "--dataset", "blocks"]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["ok"] is True
    assert report["committed_files"] == 1

    assert main([*base, "stats"]) == 0
    (stats,) = json.loads(capsys.readouterr().out)
    assert stats["table"] == "ethereum.blocks"
    assert stats["rows"] == 2
    assert stats["bytes_per_row"] == stats["bytes"] / 2


def test_verify_exits_1_on_a_missing_file(
    root: StorageRoot, capsys: pytest.CaptureFixture[str]
) -> None:
    (part,) = (root.root / "bronze").rglob("part-*")
    os.unlink(part)
    code = main(
        [
            "--data-root",
            str(root.root),
            "verify",
            "--layer",
            "bronze",
            "--chain",
            "ethereum",
            "--dataset",
            "blocks",
        ]
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["missing"]


def test_missing_root_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--data-root", str(tmp_path / "none"), "stats"]) == 2
    assert "does not exist" in capsys.readouterr().err
