"""Bounded newest-first pagination for Storage listings."""

from __future__ import annotations

import heapq
from collections.abc import Iterable

from capture_node.core.storages.contracts import StorageListResult, StoredFile


def newest_page(
    files: Iterable[StoredFile],
    page: int,
    page_size: int,
) -> StorageListResult:
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= page_size <= 50:
        raise ValueError("page_size must be between 1 and 50")

    limit = page * page_size
    selected: list[tuple[float, str, int, StoredFile]] = []
    total = 0
    for sequence, item in enumerate(files):
        total += 1
        entry = (
            item.modified_at.timestamp(),
            item.name,
            sequence,
            item,
        )
        if len(selected) < limit:
            heapq.heappush(selected, entry)
        elif entry[:2] > selected[0][:2]:
            heapq.heapreplace(selected, entry)

    ordered = [
        entry[3]
        for entry in sorted(selected, key=lambda entry: entry[:3], reverse=True)
    ]
    start = (page - 1) * page_size
    return StorageListResult(tuple(ordered[start : start + page_size]), total)
