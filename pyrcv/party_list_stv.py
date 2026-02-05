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


def droop_quota(total_votes: int, seats: int) -> int:
    # Q = floor(N/(S+1)) + 1
    return (total_votes // (seats + 1)) + 1


def ballot_group_mass(bg: BallotGroup) -> Fraction:
    return Fraction(bg.count, 1) * bg.weight


def compute_party_totals(ballot_groups: List[BallotGroup], num_parties: int) -> List[Fraction]:
    """Return V[0..num_parties], where index 0 is exhausted."""
    totals: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
    for bg in ballot_groups:
        p = current_party(bg)
        if p is None:
            totals[0] += ballot_group_mass(bg)
        else:
            totals[p] += ballot_group_mass(bg)
    return totals


def compute_seats_and_excess(V: List[Fraction], Q: int) -> tuple[List[int], List[Fraction]]:
    """Return (W, E) arrays aligned with V."""
    W: List[int] = [0 for _ in range(len(V))]
    E: List[Fraction] = [Fraction(0, 1) for _ in range(len(V))]
    Qf = Fraction(Q, 1)
    for i, Vi in enumerate(V):
        if i == 0:
            continue  # exhausted never earns seats
        Wi = int(Vi // Qf)  # floor for Fractions
        W[i] = Wi
        E[i] = Vi - Qf * Wi
    return W, E



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
    total_votes_int = sum(int(v) for v in race_data.votes)
    Q = droop_quota(total_votes_int, race_data.metadata.num_winners)

    V = compute_party_totals(ballot_groups, num_parties)
    W, E = compute_seats_and_excess(V, Q)
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
