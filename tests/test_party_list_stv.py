from pyrcv.party_list_stv import run_party_list_stv
from pyrcv.types import RaceData, RaceMetadata

from fractions import Fraction
from pyrcv.party_list_stv import droop_quota, compute_seats_and_excess


from pyrcv.party_list_stv import BallotGroup, compute_party_totals, transfer_surplus_for_party


def test_transfer_surplus_splits_and_conserves():
    # Two ballots: both currently on party 1
    # One goes next to 2, other has no next preference and exhausts.
    bgs = [
        BallotGroup(ranking=[1, 2], count=6, weight=Fraction(1, 1), current_index=0),
        BallotGroup(ranking=[1], count=4, weight=Fraction(1, 1), current_index=0),
    ]

    V = compute_party_totals(bgs, num_parties=2)
    assert V[1] == Fraction(10)

    # surplus 4 out of 10 -> T = 2/5
    E = [Fraction(0), Fraction(4), Fraction(0)]
    ledger = transfer_surplus_for_party(
        bgs, source_party=1, V=V, E=E, eligible_receivers={2}
    )

    # Total transferred mass = 10 * (2/5) = 4
    assert ledger.get(2) == Fraction(6) * Fraction(2, 5)  # 12/5
    assert ledger.get(0) == Fraction(4) * Fraction(2, 5)  # 8/5
    assert ledger[2] + ledger[0] == Fraction(4)

    # Now totals should reflect: party1 kept 6*(3/5) + 4*(3/5) = 6
    V2 = compute_party_totals(bgs, num_parties=2)
    assert V2[1] == Fraction(6)
    assert V2[2] == Fraction(12, 5)
    assert V2[0] == Fraction(8, 5)

    # Conservation: sum = 10
    assert V2[0] + V2[1] + V2[2] == Fraction(10)


def test_droop_quota_basic():
    assert droop_quota(100, 4) == (100 // 5) + 1  # 21


def test_compute_seats_and_excess_exact():
    Q = 10
    V = [Fraction(0), Fraction(27), Fraction(9), Fraction(10)]
    W, E = compute_seats_and_excess(V, Q)
    assert W == [0, 2, 0, 1]
    assert E == [Fraction(0), Fraction(7), Fraction(9), Fraction(0)]


def test_party_list_stv_imports_and_runs():
    md = RaceMetadata(race_name="test", num_winners=1, names=["A", "B"])
    race = RaceData(metadata=md, ballots=[[1], [2], [1, 2]], votes=[1, 1, 3])

    result = run_party_list_stv(race)

    assert result.metadata == md
    assert len(result.rounds) == 1
    assert len(result.rounds[0].count) == len(md.names) + 1  # includes exhausted slot 0

