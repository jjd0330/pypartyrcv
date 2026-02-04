from __future__ import annotations

from fractions import Fraction
from typing import Dict, List

from pyrcv.types import RaceData, RaceResult, RoundResult


def _first_nonzero(ranking: List[int]) -> int:
    """Return first non-zero preference, or 0 if none."""
    for c in ranking:
        if c != 0:
            return c
    return 0


def run_party_list_stv(race_data: RaceData, *, seed: int | None = None) -> RaceResult:
    """
    Party-list STV-like tabulation (skeleton).

    For now: returns a single round containing first-preference totals only.
    This is just to establish the module boundary + test harness cleanly.

    Notes:
    - Candidate/party indices are 1..N, with 0 reserved for exhausted/blank. :contentReference[oaicite:7]{index=7}
    - Internally we can use Fraction for exactness, then convert to float for RoundResult.
    """
    num_parties = len(race_data.metadata.names)

    totals: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
    for ranking, v in zip(race_data.ballots, race_data.votes):
        src = _first_nonzero(ranking)
        totals[src] += Fraction(int(v), 1)

    round0 = RoundResult(
        count=[float(x) for x in totals],
        elected=[],
        eliminated=[],
        transfers={},  # src_party -> {tgt_party: votes_transferred}
    )
    return RaceResult(race_data.metadata, [round0])
