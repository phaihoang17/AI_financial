"""Minimal RSS-overflow probe for the M7 memory-limit tests only.

Kept separate from ``abuse_probe`` so the memory worker cold-starts importing
only the resource-limit module, reaching an over-limit RSS with wide wall-clock
margin. The parent RSS monitor — unchanged — then deterministically classifies
the kill as ``MEMORY_LIMIT`` instead of losing a timing race to the production
wall-clock timeout. No runtime limit or enforcement path is altered.

The worker allocates continuously with non-zero fill and no sleeps. Continuous
growth keeps the pages warm and resident, which matters on macOS: idle or
zero-filled pages are compressed by the OS memory compressor and would drop the
reported resident size back under the limit. A fast, warm, non-zero climb makes
resident memory cross the limit quickly and stably, so the parent kills the
worker as MEMORY_LIMIT while it is still climbing.
"""

from __future__ import annotations

import sys

from src.sandbox.limits import apply_worker_resource_limits

_CHUNK_BYTES = 16 * 1024 * 1024
_NONZERO_FILL = b"\xa5" * _CHUNK_BYTES


def main() -> int:
    apply_worker_resource_limits()
    # Grow resident memory continuously past the RSS limit. The parent RSS
    # monitor kills the worker mid-climb; this loop only ends by that kill.
    blocks = []
    while True:
        blocks.append(bytearray(_NONZERO_FILL))  # a fresh resident, non-zero page range


if __name__ == "__main__":
    raise SystemExit(main())
