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


def advance_to_next_eligible(bg: BallotGroup, eligible: set[int]) -> Optional[int]:
    """
    Advance bg until it points to an eligible party, or exhaust.
    Returns the new current party (or None if exhausted).
    """
    while True:
        p = current_party(bg)
        if p is None:
            return None
        if p in eligible:
            return p
        advance_ballot(bg)


def transfer_surplus_for_party(
    ballot_groups: List[BallotGroup],
    source_party: int,
    V: List[Fraction],
    E: List[Fraction],
    eligible_receivers: set[int],
) -> dict[int, Fraction]:
    """
    Redistribute the surplus of source_party.

    Transfer fraction: T = E[source]/V[source]
    Applied to ballots currently allocated to source_party.

    Mutates ballot_groups by:
    - reducing weight on ballots that stay with source_party
    - creating new ballot groups (same ranking, same count) that carry the transferred weight
      and advance to next eligible receiver

    Returns a dict: target_party -> transferred vote mass (Fraction), plus key 0 for exhausted if any.
    """
    transferred_to: dict[int, Fraction] = {}
    if V[source_party] == 0 or E[source_party] == 0:
        return transferred_to

    T = E[source_party] / V[source_party]
    if T <= 0:
        return transferred_to

    new_groups: List[BallotGroup] = []

    for bg in ballot_groups:
        if current_party(bg) != source_party:
            continue

        original_weight = bg.weight
        transfer_weight = original_weight * T
        retained_weight = original_weight - transfer_weight

        # Keep retained portion on the original group
        bg.weight = retained_weight

        if transfer_weight == 0:
            continue

        # Create a new group for the transferred portion
        bg2 = BallotGroup(
            ranking=bg.ranking,
            count=bg.count,
            weight=transfer_weight,
            current_index=bg.current_index,
        )
        advance_ballot(bg2)  # move off the source party
        p2 = advance_to_next_eligible(bg2, eligible_receivers)

        key = 0 if p2 is None else p2
        mass = ballot_group_mass(bg2)
        transferred_to[key] = transferred_to.get(key, Fraction(0, 1)) + mass

        new_groups.append(bg2)

    ballot_groups.extend(new_groups)
    return transferred_to


def run_party_list_stv(race_data: RaceData, *, seed: int | None = None) -> RaceResult:
    """
    Party-list STV-like tabulation (partial implementation).

    Currently implemented:
    - Build ballot groups with exact Fraction weights
    - Compute Droop quota
    - Process *seat-winner surplus* once per winning party
    - Mark processed winners as semiclosed (they never receive transfers again)

    Not implemented yet:
    - elimination loop (W=0 only)
    - remainder-seat allocation
    - round-by-round reporting (we return a single final-count round)
    """
    num_parties = len(race_data.metadata.names)

    # Build ballot groups
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

    # Party states (eliminated unused yet)
    open_parties: set[int] = set(range(1, num_parties + 1))
    semiclosed: set[int] = set()
    eliminated: set[int] = set()

    processed: set[int] = set()

    # Seat-winner processing loop (surplus transfer -> semiclosed)
    while True:
        V = compute_party_totals(ballot_groups, num_parties)
        W, E = compute_seats_and_excess(V, Q)

        candidates = [p for p in open_parties if W[p] > 0 and p not in processed]
        if not candidates:
            break

        # deterministic choice for now: smallest party index
        p = min(candidates)

        eligible_receivers = set(open_parties)
        eligible_receivers.discard(p)

        transfer_surplus_for_party(
            ballot_groups=ballot_groups,
            source_party=p,
            V=V,
            E=E,
            eligible_receivers=eligible_receivers,
        )

        open_parties.remove(p)
        semiclosed.add(p)
        processed.add(p)

    # Final totals after winner processing
    V_final = compute_party_totals(ballot_groups, num_parties)

    round0 = RoundResult(
        count=[float(x) for x in V_final],
        elected=[],
        eliminated=[],
        transfers={},
    )
    return RaceResult(race_data.metadata, [round0])
