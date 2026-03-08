from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Optional, List
import os
import re
import tempfile

from flask import Flask, request, render_template_string
import plotly.graph_objects as go

import pyrcv
from pyrcv import transform
from pyrcv.party_list_stv import run_party_list_stv
from pyrcv.types import RaceData, RaceMetadata


app = Flask(__name__)

GENERAL_BALLOT_PREFIX = "*General Ballot*"
LIST_BALLOT_PREFIX = "*List Ballot*"
PARTY_TAG_PATTERN = re.compile(r"\{([^}]+)\}")
WINNERS_PATTERN = re.compile(r"\((\d+)\s+(?:winners?|WINNERS?|Winners?)\)")


@dataclass
class PartyCandidateTabulation:
    party_name: str
    seats_won: int
    contributing_districts: int
    skipped_zero_vote_districts: List[str]
    quota: float
    race_data: RaceData
    result: pyrcv.RaceResult


# ----------------------------
# Core helpers
# ----------------------------

def _run_standard_stv(race_data, seed: Optional[int]):
    if hasattr(pyrcv, "run_rcv"):
        return pyrcv.run_rcv(race_data, seed=seed)  # type: ignore
    if hasattr(pyrcv, "run_stv"):
        return pyrcv.run_stv(race_data, seed=seed)  # type: ignore
    from pyrcv.pyrcv import run_rcv as f  # type: ignore
    return f(race_data, seed=seed)


def _seat_counter(result) -> Counter:
    c = Counter()
    for rnd in result.rounds:
        for p in rnd.elected:
            c[int(p)] += 1
    return c


def _droop_quota(total_votes: int, seats: int) -> int:
    return (total_votes // (seats + 1)) + 1


def _float_fmt(x: float) -> str:
    return f"{float(x):.6f}"


def _elected_name_order(result, names: List[str]) -> List[str]:
    out: List[str] = []
    for rnd in result.rounds:
        for idx in rnd.elected:
            if 1 <= idx <= len(names):
                out.append(names[idx - 1])
    return out


def _race_has_prefix(race: RaceData, prefix: str) -> bool:
    return getattr(race.metadata, "race_name", "").strip().startswith(prefix)


def _extract_party_name(race_name: str) -> Optional[str]:
    m = PARTY_TAG_PATTERN.search(race_name)
    if not m:
        return None
    return m.group(1).strip()


def _extract_district_label(race_name: str) -> str:
    text = race_name.strip()
    if text.startswith(LIST_BALLOT_PREFIX):
        text = text[len(LIST_BALLOT_PREFIX):].strip()
    text = PARTY_TAG_PATTERN.sub("", text).strip()
    text = WINNERS_PATTERN.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Unlabeled district"


def _load_all_races(csv_path: str) -> List[RaceData]:
    races = transform.parse_google_form_csv(csv_path)
    if not isinstance(races, list):
        raise TypeError("Expected parse_google_form_csv() to return a list of races.")
    return races


def _override_num_winners(race: RaceData, num_winners: int) -> RaceData:
    md = race.metadata
    md2 = RaceMetadata(
        race_name=md.race_name,
        num_winners=int(num_winners),
        names=list(md.names),
    )
    return RaceData(metadata=md2, ballots=race.ballots, votes=race.votes)


def _pick_largest_non_special_race(races: List[RaceData]) -> RaceData:
    ordinary = [
        r for r in races
        if not _race_has_prefix(r, GENERAL_BALLOT_PREFIX)
        and not _race_has_prefix(r, LIST_BALLOT_PREFIX)
    ]
    if ordinary:
        return max(ordinary, key=lambda r: sum(int(v) for v in r.votes))

    general_races = [r for r in races if _race_has_prefix(r, GENERAL_BALLOT_PREFIX)]
    if len(general_races) == 1:
        return general_races[0]

    if len(general_races) > 1:
        raise ValueError(
            f"Could not identify a single fallback race to tabulate: found {len(general_races)} {GENERAL_BALLOT_PREFIX} races and no ordinary race."
        )

    raise ValueError("Could not identify a race to tabulate.")


def _build_general_result_rows(general_race: RaceData, general_result) -> tuple[list[tuple[str, int]], str, list[tuple[str, str]]]:
    names = list(general_race.metadata.names)
    seats_c = _seat_counter(general_result)
    seat_rows = [
        (names[i - 1], int(seats_c.get(i, 0)))
        for i in range(1, len(names) + 1)
        if seats_c.get(i, 0) > 0
    ]
    last = general_result.rounds[-1].count
    final_exhausted = _float_fmt(last[0])
    final_rows = [(names[i - 1], _float_fmt(last[i])) for i in range(1, len(names) + 1)]
    return seat_rows, final_exhausted, final_rows


def _group_list_races_by_party(list_races: List[RaceData]) -> Dict[str, List[RaceData]]:
    grouped: Dict[str, List[RaceData]] = defaultdict(list)
    for race in list_races:
        party_name = _extract_party_name(race.metadata.race_name)
        if not party_name:
            raise ValueError(f"List ballot race is missing a {{Party Name}} tag: {race.metadata.race_name}")
        grouped[party_name].append(race)
    return dict(grouped)


def _build_weighted_candidate_race(
    *,
    party_name: str,
    party_races: List[RaceData],
    seats_won: int,
) -> tuple[RaceData, int, List[str]]:
    if seats_won <= 0:
        raise ValueError("seats_won must be positive for candidate ordering")

    candidate_pairs: List[tuple[str, str]] = []
    for race in party_races:
        district = _extract_district_label(race.metadata.race_name)
        for name in race.metadata.names:
            candidate_pairs.append((district, name))

    raw_name_counts = Counter(name for _, name in candidate_pairs)

    all_names: List[str] = []
    remap_by_race: Dict[int, Dict[int, int]] = {}
    next_idx = 1
    pair_cursor = 0
    for race_idx, race in enumerate(party_races):
        mapping: Dict[int, int] = {}
        for old_idx, name in enumerate(race.metadata.names, start=1):
            district, raw_name = candidate_pairs[pair_cursor]
            pair_cursor += 1
            label = raw_name if raw_name_counts[raw_name] == 1 else f"{raw_name} [{district}]"
            all_names.append(label)
            mapping[old_idx] = next_idx
            next_idx += 1
        remap_by_race[race_idx] = mapping

    combined_ballots: List[List[int]] = []
    combined_votes: List[float] = []
    contributing_districts = 0
    skipped_zero_vote_districts: List[str] = []

    for race_idx, race in enumerate(party_races):
        district = _extract_district_label(race.metadata.race_name)
        mapping = remap_by_race[race_idx]

        # Only count ballots that actually cast at least one valid ranking in this
        # party's list ballot. Blank rows from voters who selected a different party
        # on the general ballot should not dilute this district's normalized weight.
        valid_ballot_entries: List[tuple[List[int], float]] = []
        district_valid_votes = 0.0

        for ranking, vote_count in zip(race.ballots, race.votes):
            mapped = [mapping[c] for c in ranking if c != 0]
            if not mapped:
                continue
            vc = float(vote_count)
            if vc <= 0:
                continue
            valid_ballot_entries.append((mapped, vc))
            district_valid_votes += vc

        if district_valid_votes <= 0:
            # This district is skipped only because it contains no valid ballots for this
            # party's list ballot. With no ballots present, there is no ballot weight to
            # normalize and therefore no district vote mass to add to the candidate count.
            skipped_zero_vote_districts.append(district)
            continue

        contributing_districts += 1

        for mapped, vote_count in valid_ballot_entries:
            weight = vote_count / district_valid_votes
            combined_ballots.append(mapped)
            combined_votes.append(weight)

    if contributing_districts <= 0:
        raise ValueError(
            f"Party '{party_name}' won {seats_won} seat(s), but none of its list-ballot districts had any valid ballots."
        )

    race_data = RaceData(
        metadata=RaceMetadata(
            race_name=f"{LIST_BALLOT_PREFIX} {{{party_name}}} statewide candidate ordering",
            num_winners=seats_won,
            names=all_names,
        ),
        ballots=combined_ballots,
        votes=combined_votes,  # type: ignore[arg-type]
    )

    # Sanity check: total vote mass should equal number of contributing districts
    total_mass = sum(combined_votes)
    if abs(total_mass - contributing_districts) > 1e-6:
        raise RuntimeError(
            f"Stage-2 vote mass mismatch: expected {contributing_districts}, got {total_mass}"
        )

    return race_data, contributing_districts, skipped_zero_vote_districts

def _run_integrated_party_list_election(all_races: List[RaceData], seed: Optional[int], total_seats: int):
    general_races = [r for r in all_races if _race_has_prefix(r, GENERAL_BALLOT_PREFIX)]
    list_races = [r for r in all_races if _race_has_prefix(r, LIST_BALLOT_PREFIX)]

    if not general_races:
        raise ValueError(
            f"Detected {LIST_BALLOT_PREFIX} race(s), but no {GENERAL_BALLOT_PREFIX} race was found."
        )
    if len(general_races) != 1:
        raise ValueError(
            f"Expected exactly one {GENERAL_BALLOT_PREFIX} race, but found {len(general_races)}."
        )

    general_race = _override_num_winners(general_races[0], total_seats)
    general_result = run_party_list_stv(general_race, seed=seed)
    general_party_seats = _seat_counter(general_result)
    general_party_names = list(general_race.metadata.names)
    list_races_by_party = _group_list_races_by_party(list_races)

    party_tabs: List[PartyCandidateTabulation] = []
    missing_parties: List[str] = []

    for party_idx, party_name in enumerate(general_party_names, start=1):
        seats_won = int(general_party_seats.get(party_idx, 0))
        if seats_won <= 0:
            continue

        party_races = list_races_by_party.get(party_name, [])
        if not party_races:
            missing_parties.append(party_name)
            continue

        candidate_race, contributing_districts, skipped_zero_vote_districts = _build_weighted_candidate_race(
            party_name=party_name,
            party_races=party_races,
            seats_won=seats_won,
        )
        quota = contributing_districts / float(seats_won + 1)
        candidate_result = pyrcv.run_rcv(candidate_race, seed=seed, custom_quota=quota)
        party_tabs.append(
            PartyCandidateTabulation(
                party_name=party_name,
                seats_won=seats_won,
                contributing_districts=contributing_districts,
                skipped_zero_vote_districts=skipped_zero_vote_districts,
                quota=quota,
                race_data=candidate_race,
                result=candidate_result,
            )
        )

    return {
        "general_race": general_race,
        "general_result": general_result,
        "party_tabs": party_tabs,
        "missing_parties": missing_parties,
    }


# ----------------------------
# Option 1: Quota-block chart
# ----------------------------

def _quota_block_chart_html(
    *,
    names: List[str],
    final_votes: List[float],
    seats_c: Counter,
    quota: float,
) -> str:
    P = len(names)
    Q = float(quota)

    labels = names
    votes = [float(final_votes[i]) for i in range(1, P + 1)]
    seats = [int(seats_c.get(i, 0)) for i in range(1, P + 1)]
    locked = [Q * s for s in seats]
    remainder = [v - l for v, l in zip(votes, locked)]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Locked (Q × seats)",
            x=labels,
            y=locked,
            hovertemplate="Party=%{x}<br>Locked=%{y:.6f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            name="Remainder (V − locked)",
            x=labels,
            y=remainder,
            hovertemplate="Party=%{x}<br>Remainder=%{y:.6f}<extra></extra>",
        )
    )

    max_y = max([0.0] + votes + locked)
    if quota > 0:
        kmax = int(max_y // Q) + 2
        shapes = []
        for k in range(1, kmax + 1):
            y = k * Q
            shapes.append(
                dict(
                    type="line",
                    xref="paper",
                    x0=0,
                    x1=1,
                    yref="y",
                    y0=y,
                    y1=y,
                    line=dict(width=1),
                )
            )
        fig.update_layout(shapes=shapes)

    fig.update_layout(
        barmode="stack",
        title=f"Quota blocks (Q={quota:.6f})",
        xaxis_title="Party",
        yaxis_title="Votes",
        legend_title="Components",
        height=520,
        margin=dict(l=40, r=20, t=60, b=140),
    )
    return fig.to_html(include_plotlyjs="cdn", full_html=False)


# ----------------------------
# Option 2: Round-by-round Sankey
# ----------------------------

def _round_sankey_html(result, names: List[str]) -> str:
    node_labels = ["Exhausted"] + list(names)
    P = len(names)

    per_round_links = []
    for rnd in result.rounds:
        srcs = []
        tgts = []
        vals = []
        transfers = rnd.transfers or {}
        for src, tmap in transfers.items():
            s = int(src)
            for tgt, v in tmap.items():
                t = int(tgt)
                val = float(v)
                if val <= 0:
                    continue
                if 0 <= s <= P and 0 <= t <= P:
                    srcs.append(s)
                    tgts.append(t)
                    vals.append(val)
        per_round_links.append((srcs, tgts, vals))

    init_src, init_tgt, init_val = per_round_links[0] if per_round_links else ([], [], [])

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(label=node_labels, pad=18, thickness=18),
                link=dict(source=init_src, target=init_tgt, value=init_val),
            )
        ]
    )

    buttons = []
    for i, (srcs, tgts, vals) in enumerate(per_round_links):
        buttons.append(
            dict(
                label=f"Round {i}",
                method="update",
                args=[
                    {"link": dict(source=srcs, target=tgts, value=vals)},
                    {"title": f"Transfers — Round {i}"},
                ],
            )
        )

    fig.update_layout(
        title="Transfers — Round 0",
        updatemenus=[dict(type="dropdown", buttons=buttons, x=1.02, y=1.0)],
        height=520,
        margin=dict(l=20, r=220, t=60, b=20),
    )
    return fig.to_html(include_plotlyjs="cdn", full_html=False)


# ----------------------------
# HTML
# ----------------------------

INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>pyPartyRCV – Tabulator</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; max-width: 1100px; }
      .row { display: flex; gap: 1rem; flex-wrap: wrap; }
      .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 0.5rem; text-align: left; vertical-align: top; }
      .muted { color: #666; }
    </style>
  </head>
  <body>
    <h1>pyPartyRCV – CSV Tabulator</h1>
    <p class="muted">
      Upload the same CSV format used by pyrcv’s built-in CSV parser (Google Forms export).
      Electoral system and math/algorithms designed by JJ DeFeo.
    </p>

    <form action="/analyze" method="post" enctype="multipart/form-data" class="card">
      <div class="row">
        <div>
          <label><b>CSV file</b></label><br>
          <input type="file" name="file" required>
        </div>
        <div>
          <label><b>Seats</b></label><br>
          <input type="number" name="seats" min="1" value="15" required>
        </div>
        <div>
          <label><b>Method</b></label><br>
          <select name="method">
            <option value="partylist">Party list STV</option>
            <option value="stv">Classic STV (pyrcv)</option>
          </select>
        </div>
        <div>
          <label><b>Seed (optional)</b></label><br>
          <input type="number" name="seed">
        </div>
      </div>
      <p><button type="submit">Analyze</button></p>
    </form>
  </body>
</html>
"""


RESULT_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Results</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; max-width: 980px; }
      .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem; margin: 1rem 0; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 0.5rem; text-align: left; }
      .muted { color: #666; }
      a { color: inherit; }
    </style>
  </head>
  <body>
    <p><a href="/">← back</a></p>
    <h1>Results</h1>
    <p class="muted">
      Method: <b>{{ method }}</b> |
      Seats: <b>{{ seats }}</b> |
      N: <b>{{ N }}</b> |
      Q: <b>{{ Q }}</b>
    </p>

    <div class="card">
      <h2>Seat totals</h2>
      <table>
        <tr><th>Party</th><th>Seats</th></tr>
        {% for name, s in seat_rows %}
          <tr><td>{{ name }}</td><td>{{ s }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h2>Final totals (last round)</h2>
      <table>
        <tr><th>Party</th><th>Votes</th></tr>
        <tr><td>Exhausted</td><td>{{ final_exhausted }}</td></tr>
        {% for name, v in final_rows %}
          <tr><td>{{ name }}</td><td>{{ v }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h2>Quota blocks</h2>
      <p class="muted">Locked votes are Q × seats. Remainder is V − locked.</p>
      {{ quota_blocks|safe }}
    </div>

    <div class="card">
      <h2>Transfers by round</h2>
      <p class="muted">Use the dropdown to view transfers one round at a time.</p>
      {{ sankey_by_round|safe }}
    </div>
  </body>
</html>
"""


INTEGRATED_RESULT_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Integrated Party-List Results</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; max-width: 1100px; }
      .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem; margin: 1rem 0; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 0.5rem; text-align: left; vertical-align: top; }
      .muted { color: #666; }
      .warning { color: #8a5a00; }
      a { color: inherit; }
      ol { margin-top: 0.5rem; }
    </style>
  </head>
  <body>
    <p><a href="/">← back</a></p>
    <h1>Integrated Party-List Results</h1>
    <p class="muted">
      Detected <b>{{ general_prefix }}</b> and <b>{{ list_prefix }}</b> races, so the file was processed in two stages:
      the general ballot was used to allocate party seats first, and those seat totals were then carried into the candidate list-ballot counts.
    </p>

    <div class="card">
      <h2>Stage 1 — General ballot seat allocation</h2>
      <p class="muted">
        General race: <b>{{ general_race_name }}</b> |
        Total seats: <b>{{ general_seats }}</b> |
        Vote total N: <b>{{ general_N }}</b> |
        Quota Q: <b>{{ general_Q }}</b>
      </p>
      <table>
        <tr><th>Party</th><th>Seats won</th></tr>
        {% for name, s in general_seat_rows %}
          <tr><td>{{ name }}</td><td>{{ s }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h2>Stage 1 — Final party totals</h2>
      <table>
        <tr><th>Party</th><th>Votes</th></tr>
        <tr><td>Exhausted</td><td>{{ general_final_exhausted }}</td></tr>
        {% for name, v in general_final_rows %}
          <tr><td>{{ name }}</td><td>{{ v }}</td></tr>
        {% endfor %}
      </table>
    </div>

    <div class="card">
      <h2>Stage 1 — Quota blocks</h2>
      {{ general_quota_blocks|safe }}
    </div>

    <div class="card">
      <h2>Stage 1 — Transfers by round</h2>
      {{ general_sankey|safe }}
    </div>

    {% if missing_parties %}
    <div class="card">
      <h2>Missing list-ballot races</h2>
      <p class="warning">
        These parties won at least one general-election seat but had no matching {{ list_prefix }} races in the uploaded file:
        {{ missing_parties|join(', ') }}
      </p>
    </div>
    {% endif %}

    {% for tab in party_tabs %}
      <div class="card">
        <h2>Stage 2 — {{ tab.party_name }}</h2>
        <p class="muted">
          Seats to fill from general ballot: <b>{{ tab.seats_won }}</b> |
          Contributing districts D: <b>{{ tab.contributing_districts }}</b> |
          Candidate quota Q = D / (W + 1): <b>{{ tab.quota }}</b>
        </p>
        {% if tab.skipped_zero_vote_districts %}
          <p class="muted">
            Districts skipped only because they had zero valid list-ballot votes for this party, so there was no ballot weight to normalize:
            {{ tab.skipped_zero_vote_districts|join(', ') }}
          </p>
        {% endif %}
        <p><b>Elected candidates, in order:</b></p>
        <ol>
          {% for name in tab.elected_order %}
            <li>{{ name }}</li>
          {% endfor %}
        </ol>
        <table>
          <tr><th>Candidate</th><th>Votes at election</th></tr>
          {% for name, v in tab.final_rows %}
            <tr><td>{{ name }}</td><td>{{ v }}</td></tr>
          {% endfor %}
        </table>
      </div>

      <div class="card">
        <h3>{{ tab.party_name }} — Transfers by round</h3>
        {{ tab.sankey|safe }}
      </div>
    {% endfor %}
  </body>
</html>
"""


# ----------------------------
# Routes
# ----------------------------

@app.get("/")
def index():
    return INDEX_HTML


@app.post("/analyze")
def analyze():
    f = request.files.get("file")
    if f is None:
        return "No file uploaded.", 400

    seats = int(request.form.get("seats", "15"))
    method = request.form.get("method", "stv").lower()
    seed_raw = request.form.get("seed", "")
    seed = int(seed_raw) if seed_raw.strip() else None

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "upload.csv")
        f.save(path)

        all_races = _load_all_races(path)
        has_list_ballot = any(_race_has_prefix(r, LIST_BALLOT_PREFIX) for r in all_races)

        if has_list_ballot:
            integrated = _run_integrated_party_list_election(all_races, seed, seats)
            general_race = integrated["general_race"]
            general_result = integrated["general_result"]
            party_tabs_raw = integrated["party_tabs"]
            missing_parties = integrated["missing_parties"]

            general_names = list(general_race.metadata.names)
            general_seat_rows, general_final_exhausted, general_final_rows = _build_general_result_rows(general_race, general_result)
            general_last = general_result.rounds[-1].count
            general_N = int(sum(int(v) for v in general_race.votes))
            general_Q = _droop_quota(general_N, int(general_race.metadata.num_winners))
            general_quota_blocks = _quota_block_chart_html(
                names=general_names,
                final_votes=general_last,
                seats_c=_seat_counter(general_result),
                quota=float(general_Q),
            )
            general_sankey = _round_sankey_html(general_result, general_names)

            party_tabs = []
            for tab in party_tabs_raw:
                names = list(tab.race_data.metadata.names)

                # Record each candidate's tally in the round they were first elected.
                election_votes = {}
                for rnd in tab.result.rounds:
                    for cand in rnd.elected:
                        if cand not in election_votes:
                            election_votes[cand] = rnd.count[cand]

                elected_order = _elected_name_order(tab.result, names)

                rows = []
                for i, name in enumerate(names, start=1):
                    if i in election_votes:
                        rows.append((name, _float_fmt(election_votes[i])))
                    else:
                        rows.append((name, ""))

                party_tabs.append({
                    "party_name": tab.party_name,
                    "seats_won": tab.seats_won,
                    "contributing_districts": tab.contributing_districts,
                    "skipped_zero_vote_districts": tab.skipped_zero_vote_districts,
                    "quota": _float_fmt(tab.quota),
                    "elected_order": elected_order,
                    "final_rows": rows,
                    "sankey": _round_sankey_html(tab.result, names),
                })

            return render_template_string(
                INTEGRATED_RESULT_HTML,
                general_prefix=GENERAL_BALLOT_PREFIX,
                list_prefix=LIST_BALLOT_PREFIX,
                general_race_name=general_race.metadata.race_name,
                general_seats=int(general_race.metadata.num_winners),
                general_N=general_N,
                general_Q=general_Q,
                general_seat_rows=general_seat_rows,
                general_final_exhausted=general_final_exhausted,
                general_final_rows=general_final_rows,
                general_quota_blocks=general_quota_blocks,
                general_sankey=general_sankey,
                party_tabs=party_tabs,
                missing_parties=missing_parties,
            )

        if method == "partylist":
            race_data = _override_num_winners(_pick_largest_non_special_race(all_races), seats)
            result = run_party_list_stv(race_data, seed=seed)
        else:
            race_data = _override_num_winners(_pick_largest_non_special_race(all_races), seats)
            result = _run_standard_stv(race_data, seed=seed)

        names = list(race_data.metadata.names)
        P = len(names)
        seats_c = _seat_counter(result)
        seat_rows = [(names[i - 1], int(seats_c.get(i, 0))) for i in range(1, P + 1) if seats_c.get(i, 0) > 0]

        last = result.rounds[-1].count
        final_exhausted = _float_fmt(last[0])
        final_rows = [(names[i - 1], _float_fmt(last[i])) for i in range(1, P + 1)]

        N = int(sum(int(v) for v in race_data.votes))
        S = int(race_data.metadata.num_winners)
        Q = _droop_quota(N, S)

        quota_blocks = _quota_block_chart_html(
            names=names,
            final_votes=last,
            seats_c=seats_c,
            quota=float(Q),
        )
        sankey_by_round = _round_sankey_html(result, names)

        return render_template_string(
            RESULT_HTML,
            method=method,
            seats=seats,
            N=N,
            Q=Q,
            seat_rows=seat_rows,
            final_exhausted=final_exhausted,
            final_rows=final_rows,
            quota_blocks=quota_blocks,
            sankey_by_round=sankey_by_round,
        )


def main():
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()