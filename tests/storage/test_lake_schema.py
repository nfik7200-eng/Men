from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from bip.core.exceptions import RowValidationError
from bip.storage.lake.partition import Partition, block_range_part, date_part
from bip.storage.lake.schema import Column, ColumnType, TableSchema
from bip.storage.paths import Layer

BLOCKS = TableSchema(
    dataset="blocks",
    version=1,
    columns=(
        Column("number", ColumnType.INT64, nullable=False),
        Column("hash", ColumnType.STRING, nullable=False),
        Column("miner", ColumnType.STRING),
        Column("gas_used", ColumnType.INT64),
        Column("base_fee", ColumnType.FLOAT64),
        Column("final", ColumnType.BOOL),
        Column("payload", ColumnType.BINARY),
        Column("ts", ColumnType.TIMESTAMP),
    ),
    sort_key=("number",),
)


def row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {"number": 1, "hash": "0xaa"}
    base.update(over)
    return base


# ---------- TableSchema definition ----------


def test_missing_nullable_values_become_null_never_zero() -> None:
    (out,) = BLOCKS.validate_rows([row()])
    assert out["gas_used"] is None
    assert out["miner"] is None
    assert out["final"] is None
    assert list(out) == [c.name for c in BLOCKS.columns]


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"number": None}, "number"),
        ({"gas_used": True}, "gas_used"),  # bool is not an integer here
        ({"gas_used": "5"}, "gas_used"),
        ({"gas_used": 2**63}, "int64"),
        ({"base_fee": 1}, "base_fee"),  # no silent int -> float coercion
        ({"base_fee": float("nan")}, "finite"),  # NaN is not a way to say "missing"
        ({"final": 1}, "final"),
        ({"payload": "00"}, "payload"),
        ({"ts": datetime(2026, 1, 1)}, "timezone"),
        ({"extra": 1}, "unknown column"),
    ],
)
def test_invalid_values_are_rejected_with_the_column_named(
    over: dict[str, object], needle: str
) -> None:
    with pytest.raises(RowValidationError, match=needle):
        BLOCKS.validate_rows([row(**over)])


def test_missing_required_column_is_rejected() -> None:
    with pytest.raises(RowValidationError, match="hash"):
        BLOCKS.validate_rows([{"number": 1}])


def test_timestamps_are_normalised_to_utc() -> None:
    plus2 = timezone(timedelta(hours=2))
    (out,) = BLOCKS.validate_rows([row(ts=datetime(2026, 1, 1, 12, tzinfo=plus2))])
    assert out["ts"] == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert out["ts"].tzinfo is UTC  # type: ignore[union-attr]


def test_error_message_names_row_index_and_never_the_value() -> None:
    with pytest.raises(RowValidationError) as info:
        BLOCKS.validate_rows([row(), row(hash=12345678901)])
    assert "row 1" in str(info.value)
    assert "12345678901" not in str(info.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dataset": "Blocks"},
        {"version": 0},
        {"columns": (Column("a", ColumnType.INT64), Column("a", ColumnType.STRING))},
        {"columns": (Column("bad-name", ColumnType.INT64),)},
        {"columns": (), "sort_key": ()},
        {"sort_key": ("nope",)},
        {"sort_key": ("miner",)},  # sort key columns must be non-nullable
    ],
)
def test_invalid_schema_definitions_are_rejected(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "dataset": "blocks",
        "version": 1,
        "columns": BLOCKS.columns,
        "sort_key": ("number",),
    }
    base.update(kwargs)
    with pytest.raises(ValueError):  # noqa: PT011 - each case has its own message
        TableSchema(**base)  # type: ignore[arg-type]


def test_fingerprint_is_stable_and_changes_with_the_schema() -> None:
    same = TableSchema(dataset="blocks", version=1, columns=BLOCKS.columns, sort_key=("number",))
    assert same.fingerprint == BLOCKS.fingerprint
    assert len(BLOCKS.fingerprint) == 64
    v2 = TableSchema(dataset="blocks", version=2, columns=BLOCKS.columns, sort_key=("number",))
    assert v2.fingerprint != BLOCKS.fingerprint


def test_sort_rows_and_key_ranges() -> None:
    rows = BLOCKS.validate_rows([row(number=3), row(number=1), row(number=2)])
    ordered = BLOCKS.sort_rows(rows)
    assert [r["number"] for r in ordered] == [1, 2, 3]
    assert BLOCKS.key_ranges(ordered) == {"number": [1, 3]}


def test_key_ranges_serialise_timestamps_and_bytes() -> None:
    schema = TableSchema(
        dataset="events",
        version=1,
        columns=(
            Column("ts", ColumnType.TIMESTAMP, nullable=False),
            Column("id", ColumnType.BINARY, nullable=False),
        ),
        sort_key=("ts", "id"),
    )
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    rows = schema.validate_rows([{"ts": t0, "id": b"\x02"}, {"ts": t0, "id": b"\x01"}])
    assert schema.key_ranges(rows) == {
        "ts": ["2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"],
        "id": ["01", "02"],
    }


# ---------- Partition ----------


def test_partition_relative_dir_follows_the_documented_layout() -> None:
    p = Partition(Layer.BRONZE, "ethereum", "blocks", (block_range_part(21_000_123),))
    assert p.relative_dir().as_posix() == (
        "bronze/chain=ethereum/dataset=blocks/range=021000000-021000999"
    )
    assert p.table == "ethereum.blocks"
    assert p.key == "range=021000000-021000999"


def test_date_part_and_partition_without_parts() -> None:
    assert date_part(date(2026, 11, 3)) == ("date", "2026-11-03")
    p = Partition(Layer.SILVER, "solana", "transfers")
    assert p.key == ""
    assert p.relative_dir().as_posix() == "silver/chain=solana/dataset=transfers"


@pytest.mark.parametrize(
    ("layer", "chain", "dataset", "parts"),
    [
        (Layer.TMP, "ethereum", "blocks", ()),
        (Layer.MANIFESTS, "ethereum", "blocks", ()),
        (Layer.BRONZE, "Eth", "blocks", ()),
        (Layer.BRONZE, "ethereum", "blo/cks", ()),
        (Layer.BRONZE, "ethereum", "blocks", (("range", ".."),)),
        (Layer.BRONZE, "ethereum", "blocks", (("range", "a/b"),)),
        (Layer.BRONZE, "ethereum", "blocks", (("Bad", "x"),)),
        (Layer.BRONZE, "ethereum", "blocks", (("a", "1"), ("a", "2"))),
    ],
)
def test_invalid_partitions_are_rejected(
    layer: Layer, chain: str, dataset: str, parts: tuple[tuple[str, str], ...]
) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        Partition(layer, chain, dataset, parts)


def test_block_range_part_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        block_range_part(-1)
    with pytest.raises(ValueError, match="width"):
        block_range_part(5, width=0)
    assert block_range_part(999, width=1000) == ("range", "000000000-000000999")
