from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional

from pyrcv.types import RaceData, RaceResult, RoundResult


@dataclass
class BallotGroup:
    ranking: List[int]   # ordered party preferences (1-based, no 0s)
    count: int           # original integer count
    weight: Fraction     # current per-ballot weight
    current_index: int   # index into ranking list


def current_party(bg: BallotGroup) -> Optional[int]:
    if bg.current_index >= len(bg.ranking):
        return None
    return bg.ranking[bg.current_index]


def advance_ballot(bg: BallotGroup) -> None:
    bg.current_index += 1


def _first_nonzero(ranking: List[int]) -> int:
    """Return first non-zero preference, or 0 if none."""
    for c in ranking:
        if c != 0:
            return c
    return 0


def run_party_list_stv(race_data: RaceData, *, seed: int | None = None) -> RaceResult:
    """
    Party-list STV-like tabulation (still skeleton).

    For now: returns a single round containing first-preference totals only.
    """
    num_parties = len(race_data.metadata.names)

    # Build ballot groups (will be used for real transfers later)
    ballot_groups: List[BallotGroup] = []
    for ranking, v in zip(race_data.ballots, race_data.votes):
        cleaned = [c for c in ranking if c != 0]
        if not cleaned:
            continue
        ballot_groups.append(
            BallotGroup(
                ranking=cleaned,
                count=int(v),
                weight=Fraction(1, 1),
                current_index=0,
            )
        )

    # Current behavior: first-preference totals (keeps tests simple while we build machinery)
    totals: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
    for bg in ballot_groups:
        p = current_party(bg)
        if p is None:
            totals[0] += Fraction(bg.count, 1) * bg.weight
        else:
            totals[p] += Fraction(bg.count, 1) * bg.weight

    round0 = RoundResult(
        count=[float(x) for x in totals],
        elected=[],
        eliminated=[],
        transfers={},
    )
    return RaceResult(race_data.metadata, [round0])
