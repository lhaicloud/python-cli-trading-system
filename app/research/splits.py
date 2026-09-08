"""Chronological development/validation/untouched-test splitting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ChronologicalSplit:
    development: list[T]
    validation: list[T]
    untouched_test: list[T]


def chronological_split(
    items: Sequence[T],
    development_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> ChronologicalSplit[T]:
    """Split already-chronological observations without shuffling.

    The remainder is the untouched test set. Fractions must leave a non-empty
    test allocation when enough observations exist.
    """
    if not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if development_fraction + validation_fraction >= 1:
        raise ValueError("development + validation fractions must be < 1")

    n = len(items)
    if n < 3:
        raise ValueError("need at least three chronological observations")

    dev_end = max(1, int(n * development_fraction))
    val_end = max(dev_end + 1, int(n * (development_fraction + validation_fraction)))
    val_end = min(val_end, n - 1)

    return ChronologicalSplit(
        development=list(items[:dev_end]),
        validation=list(items[dev_end:val_end]),
        untouched_test=list(items[val_end:]),
    )
