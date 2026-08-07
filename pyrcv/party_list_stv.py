from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Iterable, List, Optional, Tuple
import random

from pyrcv.types import RaceData, RaceResult, RoundResult


@dataclass
class BallotGroup:
    ranking: List[int]
    count: int
    weight: Fraction
    current_index: int


def current_party(bg: BallotGroup) -> Optional[int]:
    if bg.current_index >= len(bg.ranking):
        return None
    return bg.ranking[bg.current_index]


def advance_ballot(bg: BallotGroup) -> None:
    bg.current_index += 1


def ballot_group_mass(bg: BallotGroup) -> Fraction:
    return Fraction(bg.count, 1) * bg.weight


def droop_quota(total_votes: int, seats: int) -> int:
    return (total_votes // (seats + 1)) + 1


def _clean_ranking(raw: Iterable[int], *, num_parties: int) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for x in raw:
        if x == 0:
            continue
        if not (1 <= x <= num_parties):
            raise ValueError(f"Out-of-range party index {x} (valid 1..{num_parties}).")
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _build_ballot_groups(race_data: RaceData, *, num_parties: int) -> List[BallotGroup]:
    buckets: Dict[Tuple[int, ...], int] = {}
    for ranking, v in zip(race_data.ballots, race_data.votes):
        cnt = int(v)
        if cnt <= 0:
            continue
        cleaned = _clean_ranking(ranking, num_parties=num_parties)
        if not cleaned:
            continue
        key = tuple(cleaned)
        buckets[key] = buckets.get(key, 0) + cnt

    return [
        BallotGroup(ranking=list(k), count=cnt, weight=Fraction(1, 1), current_index=0)
        for k, cnt in buckets.items()
    ]


def compute_party_totals(ballot_groups: List[BallotGroup], num_parties: int) -> List[Fraction]:
    totals: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
    for bg in ballot_groups:
        p = current_party(bg)
        if p is None:
            totals[0] += ballot_group_mass(bg)
        else:
            totals[p] += ballot_group_mass(bg)
    return totals


def compute_seats_and_excess(V: List[Fraction], Q: int) -> Tuple[List[int], List[Fraction]]:
    W: List[int] = [0 for _ in range(len(V))]
    E: List[Fraction] = [Fraction(0, 1) for _ in range(len(V))]
    Qf = Fraction(Q, 1)
    for i in range(1, len(V)):
        Wi = int(V[i] // Qf)
        W[i] = Wi
        E[i] = V[i] - Qf * Wi
    return W, E


def advance_to_next_eligible(bg: BallotGroup, eligible: set[int]) -> Optional[int]:
    while True:
        p = current_party(bg)
        if p is None:
            return None
        if p in eligible:
            return p
        advance_ballot(bg)


def transfer_surplus_for_party(
    ballot_groups: List[BallotGroup],
    *,
    source_party: int,
    V: List[Fraction],
    surplus: Fraction,
    eligible_receivers: set[int],
) -> Dict[int, Fraction]:
    """
    Transfer only the provided surplus amount.
    Uses T = surplus / V[source] applied to ballots currently on source.
    Returns ledger target->mass (0 for exhausted).
    """
    ledger: Dict[int, Fraction] = {}
    if V[source_party] == 0 or surplus == 0:
        return ledger

    T = surplus / V[source_party]
    if T <= 0:
        return ledger

    new_groups: List[BallotGroup] = []

    for bg in ballot_groups:
        if current_party(bg) != source_party:
            continue

        w0 = bg.weight
        w_tr = w0 * T
        w_keep = w0 - w_tr

        bg.weight = w_keep

        if w_tr == 0:
            continue

        bg2 = BallotGroup(
            ranking=bg.ranking,
            count=bg.count,
            weight=w_tr,
            current_index=bg.current_index,
        )
        advance_ballot(bg2)
        p2 = advance_to_next_eligible(bg2, eligible_receivers)
        key = 0 if p2 is None else p2

        m = ballot_group_mass(bg2)
        ledger[key] = ledger.get(key, Fraction(0, 1)) + m
        new_groups.append(bg2)

    ballot_groups.extend(new_groups)
    return ledger


def eliminate_party_and_transfer_all(
    ballot_groups: List[BallotGroup],
    *,
    source_party: int,
    eligible_receivers: set[int],
) -> Dict[int, Fraction]:
    ledger: Dict[int, Fraction] = {}
    for bg in ballot_groups:
        if current_party(bg) != source_party:
            continue
        advance_ballot(bg)
        p2 = advance_to_next_eligible(bg, eligible_receivers)
        key = 0 if p2 is None else p2
        m = ballot_group_mass(bg)
        ledger[key] = ledger.get(key, Fraction(0, 1)) + m
    return ledger


def _float_transfers_round(src_to_tgt: Dict[int, Dict[int, Fraction]]) -> Dict[int, Dict[int, float]]:
    return {src: {t: float(m) for t, m in tmap.items()} for src, tmap in src_to_tgt.items()}


def run_party_list_stv(race_data: RaceData, *, seed: int | None = None) -> RaceResult:
    num_parties = len(race_data.metadata.names)
    seats_total = int(race_data.metadata.num_winners)
    if seats_total <= 0:
        raise ValueError("num_winners must be positive")

    rng = random.Random(seed)

    ballot_groups = _build_ballot_groups(race_data, num_parties=num_parties)

    N = sum(int(v) for v in race_data.votes)
    Q = droop_quota(N, seats_total)
    Qf = Fraction(Q, 1)

    open_parties: set[int] = set(range(1, num_parties + 1))
    semiclosed: set[int] = set()
    eliminated: set[int] = set()

    seats_won: List[int] = [0 for _ in range(num_parties + 1)]
    rounds: List[RoundResult] = []

    V_first = compute_party_totals(ballot_groups, num_parties)

    def snapshot(*, elected: List[int], eliminated_list: List[int], transfers: Dict[int, Dict[int, Fraction]]) -> None:
        Vnow = compute_party_totals(ballot_groups, num_parties)
        rounds.append(
            RoundResult(
                count=[float(x) for x in Vnow],
                elected=elected,
                eliminated=eliminated_list,
                transfers=_float_transfers_round(transfers),
            )
        )

    snapshot(elected=[], eliminated_list=[], transfers={})

    def seats_filled() -> int:
        return sum(seats_won)

    while open_parties and seats_filled() < seats_total:
        V = compute_party_totals(ballot_groups, num_parties)
        W_floor, E_floor = compute_seats_and_excess(V, Q)

        # A party is eligible for quota processing only if its current whole-quota
        # entitlement exceeds the seats it has already secured.
        #
        # This distinction becomes necessary once semiclosed parties can be reopened.
        # Without it, a reopened party would be awarded its existing seats again.
        winners = [
            p
            for p in open_parties
            if W_floor[p] > seats_won[p]
        ]

        if winners:
            # Process the party with:
            #   1. most whole-quota seats,
            #   2. smallest excess,
            #   3. largest current vote,
            #   4. largest first-round vote,
            #   5. seeded random tie-break.
            #
            # min() is used with negative values for criteria that should be
            # maximized. Excess remains positive because it should be minimized.
            def key(
                p: int,
            ) -> Tuple[int, Fraction, Fraction, Fraction, float]:
                return (
                    -W_floor[p],
                    E_floor[p],
                    -V[p],
                    -V_first[p],
                    rng.random(),
                )

            p = min(winners, key=key)

            remaining = seats_total - seats_filled()

            # Award only the additional whole-quota seats that this party has
            # earned since it was last processed.
            newly_earned_seats = W_floor[p] - seats_won[p]
            award = min(newly_earned_seats, remaining)
            seats_won[p] += award

            # Surplus relative to awarded seats
            surplus = V[p] - Qf * seats_won[p]
            if surplus < 0:
                surplus = Fraction(0, 1)

            eligible_receivers = set(open_parties)
            eligible_receivers.discard(p)

            ledger = transfer_surplus_for_party(
                ballot_groups,
                source_party=p,
                V=V,
                surplus=surplus,
                eligible_receivers=eligible_receivers,
            )

            # Add a self "retained" record so Sankey can show "only surplus leaves"
            retained = V[p] - surplus
            if retained > 0:
                ledger[p] = ledger.get(p, Fraction(0, 1)) + retained

            open_parties.remove(p)
            semiclosed.add(p)

            snapshot(elected=[p] * award, eliminated_list=[], transfers={p: ledger})
            continue

        # No open party currently has an additional whole quota.
        remaining = seats_total - seats_filled()

        # First apply the ordinary stopping rule using the parties that
        # are currently open.
        #
        # If the number of open parties is no greater than the number
        # of remaining seats, stop eliminating and proceed to the
        # remainder-seat stage.
        if len(open_parties) <= remaining:
            break

        # If every currently open party has already secured at least
        # one seat, then the smallest remaining party already has a
        # seat. No further eliminations should occur.
        #
        # All quota surpluses have already been processed because this
        # section is reached only when "winners" is empty.
        seatless_open_parties = [
            p
            for p in open_parties
            if seats_won[p] == 0
        ]

        if not seatless_open_parties:
            break

        # An elimination is going to occur, so reopen all semiclosed
        # parties before choosing the loser.
        #
        # This allows the eliminated party's ballots to transfer to
        # parties that previously secured quota seats.
        if semiclosed:
            open_parties.update(semiclosed)
            semiclosed.clear()

        # Recalculate current totals after reopening.
        #
        # Reopening does not itself change any ballot weights, but it
        # changes which parties are eligible to receive transfers.
        V = compute_party_totals(ballot_groups, num_parties)

        # Only parties that have not secured a seat may be eliminated.
        #
        # If no seatless parties remain after reopening, the smallest
        # surviving party has a seat, so elimination ends.
        elimination_candidates = [
            p
            for p in open_parties
            if seats_won[p] == 0
        ]

        if not elimination_candidates:
            break

        # Eliminate the lowest-current-vote seatless party.
        #
        # Ties are resolved by:
        #   1. lower current vote,
        #   2. lower first-round vote,
        #   3. seeded random tie-break.
        def elim_key(p: int) -> Tuple[Fraction, Fraction, float]:
            return (
                V[p],
                V_first[p],
                rng.random(),
            )

        loser = min(elimination_candidates, key=elim_key)

        # Every other open party, including reopened parties that
        # already hold seats, may receive transferred ballots.
        eligible_receivers = set(open_parties)
        eligible_receivers.discard(loser)

        ledger = eliminate_party_and_transfer_all(
            ballot_groups,
            source_party=loser,
            eligible_receivers=eligible_receivers,
        )

        open_parties.remove(loser)
        eliminated.add(loser)

        snapshot(
            elected=[],
            eliminated_list=[loser],
            transfers={loser: ledger},
        )

    # Remainder seats: allocate one-by-one using residual = V - Q*seats_won, and UPDATE residual each time
    remaining = seats_total - seats_filled()
    if remaining > 0:
        V = compute_party_totals(ballot_groups, num_parties)
        residual: List[Fraction] = [Fraction(0, 1) for _ in range(num_parties + 1)]
        for p in range(1, num_parties + 1):
            residual[p] = V[p] - Qf * seats_won[p]

        elected_batch: List[int] = []
        for _ in range(remaining):
            candidates = [
                p
                for p in range(1, num_parties + 1)
                if p not in eliminated
            ]
            if not candidates:
                break

            best_res = max(residual[p] for p in candidates)
            top = [p for p in candidates if residual[p] == best_res]

            if len(top) > 1:
                best_V = max(V[p] for p in top)
                top = [p for p in top if V[p] == best_V]

            if len(top) > 1:
                best_fr = max(V_first[p] for p in top)
                top = [p for p in top if V_first[p] == best_fr]

            pick = top[0] if len(top) == 1 else rng.choice(sorted(top))

            seats_won[pick] += 1
            residual[pick] -= Qf  # <-- CRITICAL FIX
            elected_batch.append(pick)

        snapshot(elected=elected_batch, eliminated_list=[], transfers={})

    return RaceResult(race_data.metadata, rounds)
