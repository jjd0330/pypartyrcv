from fractions import Fraction

from pyrcv.party_list_stv import (
    BallotGroup,
    droop_quota,
    compute_seats_and_excess,
    compute_party_totals,
    transfer_surplus_for_party,
    run_party_list_stv,
)
from pyrcv.types import RaceData, RaceMetadata


def _total_elected(result) -> int:
    return sum(len(r.elected) for r in result.rounds)


def test_party_list_stv_imports_and_runs():
    md = RaceMetadata(race_name="test", num_winners=1, names=["A", "B"])
    race = RaceData(metadata=md, ballots=[[1], [2], [1, 2]], votes=[1, 1, 3])

    result = run_party_list_stv(race)

    assert result.metadata == md
    assert len(result.rounds) >= 1
    assert len(result.rounds[-1].count) == len(md.names) + 1  # includes exhausted slot 0


def test_droop_quota_basic():
    assert droop_quota(100, 4) == (100 // 5) + 1  # 21


def test_compute_seats_and_excess_exact():
    Q = 10
    V = [Fraction(0), Fraction(27), Fraction(9), Fraction(10)]
    W, E = compute_seats_and_excess(V, Q)
    assert W == [0, 2, 0, 1]
    assert E == [Fraction(0), Fraction(7), Fraction(9), Fraction(0)]


def test_transfer_surplus_splits_and_conserves():
    bgs = [
        BallotGroup(ranking=[1, 2], count=6, weight=Fraction(1, 1), current_index=0),
        BallotGroup(ranking=[1], count=4, weight=Fraction(1, 1), current_index=0),
    ]

    V = compute_party_totals(bgs, num_parties=2)
    assert V[1] == Fraction(10)

    E = [Fraction(0), Fraction(4), Fraction(0)]  # surplus 4 out of 10 => T=2/5
    ledger = transfer_surplus_for_party(
        bgs, source_party=1, V=V, E=E, eligible_receivers={2}
    )

    assert ledger.get(2) == Fraction(6) * Fraction(2, 5)  # 12/5
    assert ledger.get(0) == Fraction(4) * Fraction(2, 5)  # 8/5
    assert ledger[2] + ledger[0] == Fraction(4)

    V2 = compute_party_totals(bgs, num_parties=2)
    assert V2[1] == Fraction(6)
    assert V2[2] == Fraction(12, 5)
    assert V2[0] == Fraction(8, 5)
    assert V2[0] + V2[1] + V2[2] == Fraction(10)


def test_run_allocates_all_seats_and_conserves():
    # Simple case: only first preferences, no transfers possible.
    # Remaining seats should be assigned by remainder rule (likely to the largest party).
    md = RaceMetadata(race_name="t", num_winners=2, names=["A", "B", "C"])
    race = RaceData(
        metadata=md,
        ballots=[[1], [2], [3]],
        votes=[20, 10, 11],  # N=41, S=2
    )

    result = run_party_list_stv(race, seed=0)

    assert _total_elected(result) == 2

    # Conservation: sum of final counts (including exhausted slot 0) should be N
    final_counts = result.rounds[-1].count
    assert abs(sum(final_counts) - 41.0) < 1e-9


def test_elimination_does_not_transfer_to_semiclosed():
    # Party 1 becomes semiclosed first; then party 2 is eliminated and has next pref 1.
    # Because 1 is semiclosed, 2's votes must exhaust instead of transferring to 1.
    md = RaceMetadata(race_name="t", num_winners=2, names=["A", "B", "C"])
    race = RaceData(
        metadata=md,
        ballots=[
            [1],     # A
            [2, 1],  # B would like to go to A, but A will be semiclosed
            [3],     # C
        ],
        votes=[20, 10, 11],  # N=41, Q=floor(41/3)+1=14, A wins 1 seat and semiclosed
    )

    result = run_party_list_stv(race, seed=0)

    # Find the elimination round for party 2, and ensure it did not transfer to 1.
    saw_elim2 = False
    for rnd in result.rounds:
        if rnd.eliminated == [2]:
            saw_elim2 = True
            transfers = rnd.transfers.get(2, {})
            assert 1 not in transfers
            assert 0 in transfers  # exhausted must exist (all of B should exhaust here)
    assert saw_elim2
