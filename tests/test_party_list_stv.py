from pyrcv.party_list_stv import run_party_list_stv
from pyrcv.types import RaceData, RaceMetadata


def test_party_list_stv_imports_and_runs():
    md = RaceMetadata(race_name="test", num_winners=1, names=["A", "B"])
    race = RaceData(metadata=md, ballots=[[1], [2], [1, 2]], votes=[1, 1, 3])

    result = run_party_list_stv(race)

    assert result.metadata == md
    assert len(result.rounds) == 1
    assert len(result.rounds[0].count) == len(md.names) + 1  # includes exhausted slot 0
