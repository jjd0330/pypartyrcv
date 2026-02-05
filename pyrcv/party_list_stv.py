from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Dict
import random

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
    if seats <= 0:
        raise ValueError("seats must be positive")
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
    """Return (W, E) arrays aligned with V, using W = floor(V/Q) and E = V - QW."""
    W: List[int] = [0 for _ in range(len(V))]
    E: List[Fraction] = [Fraction(0, 1) for _ in range(len(V))]
    Qf = Fraction(Q, 1)
    for i, Vi in enumerate(V):
        if i == 0:
            continue  # exhausted never earns seats
        Wi = int(Vi // Qf)
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

        # Retain part stays on source
        bg.weight = retained_weight

        if transfer_weight == 0:
            continue

        # Transferred part becomes its own group and advances
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


def eliminate_party_transfer(
    ballot_groups: List[BallotGroup],
    eliminated_party: int,
    eligible_receivers: set[int],
) -> dict[int, Fraction]:
    """
    Eliminate a party with W=0: transfer ALL its currently-held vote mass forward.

    Mutates ballot_groups in-place by advancing ballot pointers off the eliminated party.
    Returns transfer ledger (target -> mass), with key 0 for exhausted.
    """
    ledger: dict[int, Fraction] = {}
    for bg in ballot_groups:
        if current_party(bg) != eliminated_party:
            continue

        advance_ballot(bg)  # move off eliminated party
        p2 = advance_to_next_eligible(bg, eligible_receivers)

        key = 0 if p2 is None else p2
        mass = ballot_group_mass(bg)
        ledger[key] = ledger.get(key, Fraction(0, 1)) + mass

    return ledger


def _ledger_to_round_transfers(source: int, ledger: Dict[int, Fraction]) -> Dict[int, Dict[int, float]]:
    if not ledger:
        return {}
    return {source: {t: float(m) for t, m in ledger.items()}}


def run_party_list_stv(race_data: RaceData, *, seed: int | None = None) -> RaceResult:
    """
    Party-list STV-like tabulation (full implementation).

    Implemented:
    - Voters rank parties (indices 1..N, 0 reserved for exhausted)
    - Exact arithmetic with Fraction on ballot-group weights
    - Droop quota Q = floor(N/(S+1)) + 1
    - Seat-winner processing: when an OPEN party has floor(V/Q) > 0, it is assigned that many seats (capped
      by remaining seats), then its surplus is transferred, then it becomes SEMICLOSED permanently.
    - Semiclosed parties never receive transfers (only OPEN parties are eligible receivers).
    - Elimination: when no open party has W>0, eliminate the open party with lowest V (ties broken deterministically)
      and transfer all of its vote mass forward to next eligible open parties, else exhaust.
    - Remainder seats: if seats remain after transfers/eliminations, allocate them one-by-one using:
        1) highest residual (V - Q*seats_allocated_so_far)
        2) highest V
        3) highest first-round V
        4) seeded random tie-break

    Output:
    - RaceResult.rounds is a list of RoundResult objects.
      Each round corresponds to one action (a winner surplus transfer OR an elimination OR remainder allocation batch).
      RoundResult.elected includes the party index repeated once per seat gained in that round.
      RoundResult.transfers records {source_party: {target_party: transferred_mass}} for that round.
    """
    S = int(race_data.metadata.num_winners)
    num_parties = len(race_data.metadata.names)

    rng = random.Random(seed if seed is not None else 0)

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

    # Total (integer) ballots cast (used only for quota)
    total_votes_int = sum(int(v) for v in race_data.votes)
    if S <= 0:
        # Degenerate: no seats to fill; still return a single round count for completeness.
        V0 = compute_party_totals(ballot_groups, num_parties)
        r0 = RoundResult(count=[float(x) for x in V0], elected=[], eliminated=[], transfers={})
        return RaceResult(race_data.metadata, [r0])

    Q = droop_quota(total_votes_int, S)
    Qf = Fraction(Q, 1)

    # First-round totals for tie-breaks
    first_round = compute_party_totals(ballot_groups, num_parties)

    # Party states
    open_parties: set[int] = set(range(1, num_parties + 1))
    semiclosed: set[int] = set()
    eliminated: set[int] = set()

    # Seats allocated so far (index 0 unused)
    seats_alloc: List[int] = [0 for _ in range(num_parties + 1)]

    rounds: List[RoundResult] = []

    def total_seats_allocated() -> int:
        return sum(seats_alloc)

    def record_round(
        elected_list: List[int],
        eliminated_list: List[int],
        transfers: Dict[int, Dict[int, float]],
    ) -> None:
        V_now = compute_party_totals(ballot_groups, num_parties)
        rounds.append(
            RoundResult(
                count=[float(x) for x in V_now],
                elected=elected_list,
                eliminated=eliminated_list,
                transfers=transfers,
            )
        )

    # Main loop: alternate winner processing and eliminations until either seats filled or no open parties remain
    while open_parties and total_seats_allocated() < S:
        V = compute_party_totals(ballot_groups, num_parties)
        W_floor, E_floor = compute_seats_and_excess(V, Q)

        # Any open party that meets quota (W>0) gets processed and becomes semiclosed
        winners = [p for p in open_parties if W_floor[p] > 0]
        if winners:
            # Deterministic selection: smallest surplus first (then V, then first-round, then party index)
            p = min(
                winners,
                key=lambda x: (E_floor[x], V[x], first_round[x], x),
            )

            remaining = S - total_seats_allocated()

            # Assign floor seats for this party, capped by remaining seats
            desired_total_seats = min(W_floor[p], seats_alloc[p] + remaining)
            gained = desired_total_seats - seats_alloc[p]
            if gained < 0:
                gained = 0

            # Surplus relative to the seats we are actually assigning now
            surplus = V[p] - Qf * desired_total_seats
            if surplus < 0:
                surplus = Fraction(0, 1)

            # Transfer surplus to eligible OPEN parties (semiclosed excluded by definition)
            eligible_receivers = set(open_parties)
            eligible_receivers.discard(p)

            E_for_call = [Fraction(0, 1) for _ in range(num_parties + 1)]
            E_for_call[p] = surplus

            ledger = transfer_surplus_for_party(
                ballot_groups=ballot_groups,
                source_party=p,
                V=V,
                E=E_for_call,
                eligible_receivers=eligible_receivers,
            )

            # Lock in state
            seats_alloc[p] = desired_total_seats
            open_parties.remove(p)
            semiclosed.add(p)

            record_round(
                elected_list=[p] * gained,
                eliminated_list=[],
                transfers=_ledger_to_round_transfers(p, ledger),
            )
            continue

        # No winners among open parties: eliminate the lowest-V open party (W=0 by construction here)
        # Deterministic tie-break: lowest V, then lowest first-round V, then lowest party index
        p_elim = min(open_parties, key=lambda x: (V[x], first_round[x], x))

        eligible_receivers = set(open_parties)
        eligible_receivers.discard(p_elim)

        ledger = eliminate_party_transfer(
            ballot_groups=ballot_groups,
            eliminated_party=p_elim,
            eligible_receivers=eligible_receivers,
        )

        open_parties.remove(p_elim)
        eliminated.add(p_elim)

        record_round(
            elected_list=[],
            eliminated_list=[p_elim],
            transfers=_ledger_to_round_transfers(p_elim, ledger),
        )

    # Remainder seats if still unfilled
    remaining = S - total_seats_allocated()
    if remaining > 0 and num_parties > 0:
        V = compute_party_totals(ballot_groups, num_parties)

        # Residual votes after accounting for seats already allocated (including quota seats)
        residual: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
        for p in range(1, num_parties + 1):
            residual[p] = V[p] - Qf * seats_alloc[p]

        elected_remainders: List[int] = []
        for _ in range(remaining):
            candidates = list(range(1, num_parties + 1))

            best_res = max(residual[p] for p in candidates)
            top = [p for p in candidates if residual[p] == best_res]

            if len(top) > 1:
                best_V = max(V[p] for p in top)
                top = [p for p in top if V[p] == best_V]

            if len(top) > 1:
                best_fr = max(first_round[p] for p in top)
                top = [p for p in top if first_round[p] == best_fr]

            if len(top) > 1:
                # seeded random but reproducible
                p_pick = rng.choice(sorted(top))
            else:
                p_pick = top[0]

            elected_remainders.append(p_pick)
            seats_alloc[p_pick] += 1
            residual[p_pick] -= Qf  # consume this remainder capacity so it doesn't keep winning

        record_round(
            elected_list=elected_remainders,
            eliminated_list=[],
            transfers={},
        )

    # Ensure at least one round exists for downstream consumers
    if not rounds:
        V0 = compute_party_totals(ballot_groups, num_parties)
        rounds = [RoundResult(count=[float(x) for x in V0], elected=[], eliminated=[], transfers={})]

    return RaceResult(race_data.metadata, rounds)
