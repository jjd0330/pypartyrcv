from pyrcv.party_list_stv import run_party_list_stv
from pyrcv.types import RaceData, RaceMetadata

from fractions import Fraction
from pyrcv.party_list_stv import droop_quota, compute_seats_and_excess


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

