from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Tuple, Optional, List
import tempfile
import os

from flask import Flask, request, render_template_string
import plotly.graph_objects as go

from pyrcv.io import load_race_data_from_csv
from pyrcv.party_list_stv import run_party_list_stv
import pyrcv


app = Flask(__name__)


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


# ----------------------------
# Option 1: Quota-block chart
# ----------------------------

def _quota_block_chart_html(
    *,
    names: List[str],
    final_votes: List[float],   # index 0 exhausted, 1..P parties
    seats_c: Counter,
    quota: int,
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

    # Horizontal quota lines
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
        title=f"Quota blocks (Q={quota})",
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
            if not tmap:
                continue
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
# Aggregated Sankey (cleaned + spaced)
# ----------------------------

def _activity_order(result, num_parties: int) -> List[int]:
    """
    Returns a party ordering (1..P) roughly by "when they first mattered":
    - first round they appear as a transfer source OR are eliminated OR are elected
    - ties by party index
    """
    first_seen = {p: 10**9 for p in range(1, num_parties + 1)}

    for r_i, rnd in enumerate(result.rounds):
        # eliminated list
        for p in (rnd.eliminated or []):
            p = int(p)
            if 1 <= p <= num_parties:
                first_seen[p] = min(first_seen[p], r_i)

        # elected list
        for p in (rnd.elected or []):
            p = int(p)
            if 1 <= p <= num_parties:
                first_seen[p] = min(first_seen[p], r_i)

        # transfer sources
        for src in (rnd.transfers or {}).keys():
            p = int(src)
            if 1 <= p <= num_parties:
                first_seen[p] = min(first_seen[p], r_i)

    return sorted(range(1, num_parties + 1), key=lambda p: (first_seen[p], p))


def _aggregate_transfers(result, *, include_self_loops: bool) -> Dict[Tuple[int, int], float]:
    """
    Aggregate transfers across all rounds.
    include_self_loops=False removes src==tgt (retained/locked mass) which otherwise creates overlapping loops.
    """
    agg: Dict[Tuple[int, int], float] = defaultdict(float)
    for rnd in result.rounds:
        for src, tmap in (rnd.transfers or {}).items():
            s = int(src)
            for tgt, val in (tmap or {}).items():
                t = int(tgt)
                if (not include_self_loops) and (s == t):
                    continue
                agg[(s, t)] += float(val)
    return dict(agg)


def _sankey_html(names: List[str], transfer_agg: Dict[Tuple[int, int], float], *, node_order: List[int]) -> str:
    """
    Aggregated Sankey:
    - nodes are Exhausted (0) + parties
    - node_order controls vertical ordering of parties to reduce crossings
    """
    # remap party indices to a new vertical order
    # index 0 stays 0 (Exhausted)
    P = len(names)
    old_to_new = {0: 0}
    new_to_old = {0: 0}

    # party nodes start at 1
    for new_idx, old_party in enumerate(node_order, start=1):
        old_to_new[int(old_party)] = new_idx
        new_to_old[new_idx] = int(old_party)

    # node labels in new order
    node_labels = ["Exhausted"] + [names[p - 1] for p in node_order]

    srcs: List[int] = []
    tgts: List[int] = []
    vals: List[float] = []

    for (src, tgt), v in transfer_agg.items():
        if v <= 0:
            continue
        s = old_to_new.get(int(src))
        t = old_to_new.get(int(tgt))
        if s is None or t is None:
            continue
        srcs.append(s)
        tgts.append(t)
        vals.append(v)

    if not vals:
        return "<p>(No transfers recorded.)</p>"

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node=dict(
                    label=node_labels,
                    pad=28,          # more vertical spacing
                    thickness=22,    # bigger nodes
                ),
                link=dict(source=srcs, target=tgts, value=vals),
            )
        ]
    )

    fig.update_layout(
        height=820,  # much taller so thin flows do not overlap as badly
        margin=dict(l=20, r=20, t=20, b=20),
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
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; max-width: 980px; }
      .row { display: flex; gap: 1rem; flex-wrap: wrap; }
      .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 0.5rem; text-align: left; }
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

        race_data = load_race_data_from_csv(path, num_winners=seats)

        if method == "partylist":
            result = run_party_list_stv(race_data, seed=seed)
        else:
            result = _run_standard_stv(race_data, seed=seed)

        names = list(race_data.metadata.names)
        P = len(names)

        seats_c = _seat_counter(result)
        seat_rows = [(names[i - 1], int(seats_c.get(i, 0))) for i in range(1, P + 1) if seats_c.get(i, 0) > 0]

        last = result.rounds[-1].count
        final_exhausted = f"{last[0]:.6f}"
        final_rows = [(names[i - 1], f"{last[i]:.6f}") for i in range(1, P + 1)]

        N = int(sum(int(v) for v in race_data.votes))
        S = int(race_data.metadata.num_winners)
        Q = _droop_quota(N, S)

        quota_blocks = _quota_block_chart_html(
            names=names,
            final_votes=last,
            seats_c=seats_c,
            quota=Q,
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
