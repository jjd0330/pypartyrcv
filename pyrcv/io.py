from __future__ import annotations

from typing import Optional, Any, Sequence
import inspect

from pyrcv.types import RaceData, RaceMetadata
from pyrcv import transform


def _pick_one_race(races: Sequence[RaceData], *, race_name: Optional[str]) -> RaceData:
    if not races:
        raise ValueError("CSV parser returned an empty list of races.")

    if race_name:
        for r in races:
            if getattr(getattr(r, "metadata", None), "race_name", None) == race_name:
                return r

    if len(races) == 1:
        return races[0]

    # heuristic: prefer the race with the most ballot rows, then most total votes
    def score(r: RaceData) -> tuple[int, int]:
        ballot_rows = len(getattr(r, "ballots", []) or [])
        votes = getattr(r, "votes", []) or []
        try:
            total_votes = int(sum(int(v) for v in votes))
        except Exception:
            total_votes = 0
        return (ballot_rows, total_votes)

    return max(races, key=score)


def _with_num_winners(r: RaceData, *, num_winners: int) -> RaceData:
    """
    Return a copy of RaceData with metadata.num_winners overridden to num_winners.
    This avoids relying on whether RaceMetadata is mutable in your pyrcv fork.
    """
    md = r.metadata
    md2 = RaceMetadata(
        race_name=getattr(md, "race_name", "Race"),
        num_winners=int(num_winners),
        names=list(getattr(md, "names", [])),
    )
    return RaceData(metadata=md2, ballots=r.ballots, votes=r.votes)


def load_race_data_from_csv(
    csv_path: str,
    *,
    num_winners: int,
    race_name: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> RaceData:
    """
    Load RaceData from CSV using pyrcv.transform.parse_google_form_csv (Google Forms export).

    Normalizes parser return type AND forces num_winners onto the result metadata.
    """
    if not hasattr(transform, "parse_google_form_csv"):
        raise ImportError("pyrcv.transform.parse_google_form_csv not found in this package version.")

    fn = transform.parse_google_form_csv
    sig = inspect.signature(fn)
    params = sig.parameters

    kwargs: dict[str, Any] = {}

    # provide what the parser supports (varies by version)
    if race_name is not None:
        if "race_name" in params:
            kwargs["race_name"] = race_name
        elif "name" in params:
            kwargs["name"] = race_name

    if delimiter is not None:
        if "delimiter" in params:
            kwargs["delimiter"] = delimiter
        elif "sep" in params:
            kwargs["sep"] = delimiter

    # Try to pass num_winners if supported, but DO NOT trust it was applied.
    if "num_winners" in params:
        kwargs["num_winners"] = int(num_winners)
    elif "seats" in params:
        kwargs["seats"] = int(num_winners)
    elif "num_seats" in params:
        kwargs["num_seats"] = int(num_winners)

    parsed = fn(csv_path, **kwargs)

    if isinstance(parsed, RaceData):
        return _with_num_winners(parsed, num_winners=num_winners)

    if isinstance(parsed, (list, tuple)):
        chosen = _pick_one_race(parsed, race_name=race_name)
        return _with_num_winners(chosen, num_winners=num_winners)

    raise TypeError(
        f"parse_google_form_csv returned unexpected type {type(parsed)!r}; expected RaceData or list of RaceData."
    )
