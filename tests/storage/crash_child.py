"""Child process for the lake crash tests: writes one batch and dies hard at a named stage.

``python -c "from tests.storage.crash_child import main; main()" <root> <stage>``. ``os._exit``
skips ``finally`` blocks and buffers, like a kill: whatever is on disk is what a crash leaves.
"""

from __future__ import annotations

import os
import sys

from bip.storage.lake.manifest import Lineage
from bip.storage.lake.partition import Partition
from bip.storage.lake.writer import LakeWriter
from bip.storage.paths import Layer, StorageRoot
from tests.storage.lake_fakes import BLOCKS, JsonLinesCodec, roomy_guard, rows

PARTITION = Partition(Layer.BRONZE, "ethereum", "blocks", (("range", "000000000-000000999"),))


def main() -> None:
    root = StorageRoot(sys.argv[1])
    stage = sys.argv[2]

    def die(name: str) -> None:
        if name == stage:
            os._exit(137)

    writer = LakeWriter(root, codec=JsonLinesCodec(), guard=roomy_guard(root), on_stage=die)
    writer.write(BLOCKS, PARTITION, rows([1, 2, 3]), lineage=Lineage(job="crash-test"))
    os._exit(0)
