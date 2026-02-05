from __future__ import annotations

import json
from collections import Counter
from typing import Optional

import click

import pyrcv
from pyrcv.io import load_race_data_from_csv
from pyrcv.party_list_stv import run_party_list_stv


def _run_standard_stv(race_data, seed: Optional[int]):
    """
    Try a few likely entrypoints across pyrcv versions.
    """
    if hasattr(pyrcv, "run_rcv"):
        return pyrcv.run_rcv(race_data, seed=seed)  # type: ignore
    if hasattr(pyrcv, "run_stv"):
        return pyrcv.run_stv(race_data, seed=seed)  # type: ignore
    # Fallback: direct module import
    try:
        from pyrcv.pyrcv import run_rcv as f  # type: ignore
        return f(race_data, seed=seed)
    except Exception as e:
        raise RuntimeError("Could not find standard STV runner (run_rcv/run_stv).") from e


def _seat_table_from_rounds(result) -> Counter:
    seats = Counter()
    for rnd in result.rounds:
        for p in rnd.elected:
            seats[p] += 1
    return seats


@click.group()
def main() -> None:
    pass


@main.command()
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--seats", "num_winners", required=True, type=int, help="Number of seats to fill.")
@click.option(
    "--method",
    type=click.Choice(["stv", "partylist"], case_sensitive=False),
    default="stv",
    show_default=True,
    help="Tabulation method.",
)
@click.option("--seed", type=int, default=None, help="Seed for deterministic tie-breaks.")
@click.option("--json-out", "json_out", type=click.Path(dir_okay=False), default=None, help="Write RaceResult to JSON.")
def tabulate(csv_path: str, num_winners: int, method: str, seed: Optional[int], json_out: Optional[str]) -> None:
    """
    Tabulate an election from a CSV file.

    The CSV is parsed using the same parser referenced by classic pyrcv:
    transform.parse_google_form_csv().
    """
    race_data = load_race_data_from_csv(csv_path, num_winners=num_winners)

    if method.lower() == "partylist":
        result = run_party_list_stv(race_data, seed=seed)
    else:
        result = _run_standard_stv(race_data, seed=seed)

    seats = _seat_table_from_rounds(result)
    names = race_data.metadata.names

    click.echo("")
    click.echo(f"Method: {method}")
    click.echo(f"Seats:  {num_winners}")
    click.echo("")

    click.echo("Seat totals:")
    for idx in range(1, len(names) + 1):
        if seats[idx]:
            click.echo(f"  {idx:>2}  {names[idx-1]}: {seats[idx]}")

    # Basic round count print (last round)
    last = result.rounds[-1].count
    click.echo("")
    click.echo("Final (last-round) vote totals (includes exhausted at index 0):")
    click.echo(f"  0  Exhausted: {last[0]:.6f}")
    for idx in range(1, len(names) + 1):
        click.echo(f"  {idx:>2}  {names[idx-1]}: {last[idx]:.6f}")

    if json_out:
        payload = {
            "metadata": {
                "names": list(names),
                "num_winners": int(race_data.metadata.num_winners),
            },
            "rounds": [
                {
                    "count": r.count,
                    "elected": r.elected,
                    "eliminated": r.eliminated,
                    "transfers": r.transfers,
                }
                for r in result.rounds
            ],
        }
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        click.echo(f"\nWrote JSON: {json_out}")


if __name__ == "__main__":
    main()
