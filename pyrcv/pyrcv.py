"""Implementation of Single Transferable Vote."""

from __future__ import annotations

import collections
import enum
import itertools
from typing import Optional

import numpy as np
from numpy.typing import ArrayLike

from .types import PyRcvError, RaceData, RaceResult, RoundResult


class RoundMode(enum.Enum):
    r"""How to round a fractional vote threshold to win an election.

    .. math::
       votes\_needed = num\_votes / (num\_candidates + 1)
    """

    CEILING = 1
    ADD_ONE_FLOOR = 2
    FRACTIONAL = 3


EPSILON = 1e-5


def run_rcv(
    race_data: RaceData,
    round_mode: RoundMode = RoundMode.ADD_ONE_FLOOR,
    *,
    seed: Optional[int] = None,
    custom_quota: Optional[float] = None,
) -> RaceResult:
    """Run the ranked choice voting algorithm for a single election.

    :param race_data: Full information about the race parameters and votes.
    :param round_mode: The method for rounding the vote threshold.
    :param seed: Optional RNG seed for deterministic tied-last elimination.
    :param custom_quota: Optional explicit election quota. If supplied, this is
        used directly instead of deriving a quota from total votes and winners.

    :raise ValueError: Raised when round_mode has an unknown value, or when the
            ballot data has the wrong shape or contains bad values.
    :raise PyRcvError: Raised if an error is detected in the calcuations.
    :return: RCV results (winners, losers, vote transfers) for each round in
        the election.
    """
    num_cands = len(race_data.metadata.names)
    rng = np.random.default_rng(seed)

    ballots = validate_and_standardize_ballots(race_data.ballots, num_cands)
    votes = np.asarray(race_data.votes, dtype=float)[:, None]

    num_slots = num_cands + 1

    weights = np.zeros_like(ballots, float)
    weights[:, 0] = 1.0

    if custom_quota is not None:
        votes_needed = float(custom_quota)
        if not np.isfinite(votes_needed) or votes_needed <= 0:
            raise ValueError(f"custom_quota must be positive and finite, got {custom_quota!r}")
    else:
        votes_needed = np.sum(votes) / (race_data.metadata.num_winners + 1)
        if round_mode == RoundMode.CEILING:
            votes_needed = np.ceil(votes_needed)
        elif round_mode == RoundMode.ADD_ONE_FLOOR:
            votes_needed = np.floor(1 + votes_needed)
        elif round_mode == RoundMode.FRACTIONAL:
            votes_needed += EPSILON
        else:
            raise ValueError(f"round_mode is not a value in RoundMode enum: {round_mode}")

    status = np.zeros(num_slots, int)
    status[0] = -2

    valid = np.ones_like(ballots, bool)
    orig = _first_nonzero(valid, axis=1)
    round_info = []

    while np.count_nonzero(status > 0) < race_data.metadata.num_winners:
        counts = np.bincount(ballots.ravel(), (weights * votes).ravel(), minlength=num_slots)
        counts_masked = np.ma.array(counts, mask=(status != 0))
        elected_mask = counts_masked >= votes_needed

        elected = []
        eliminated = []
        to_remove = []
        multipliers = []

        if np.count_nonzero(status >= 0) == race_data.metadata.num_winners:
            elected = np.nonzero(status == 0)[0]
            status[status == 0] = 1
        elif elected_mask.any():
            to_remove = np.nonzero(elected_mask)[0]
            elected = to_remove
            status[to_remove] = 1
            multipliers = [votes_needed / counts_masked[c] for c in to_remove]
        else:
            min_counts = np.where(counts_masked == counts_masked.min())[0]
            pick = int(rng.choice(min_counts))
            to_remove = [pick]
            eliminated = to_remove
            status[to_remove] = -1
            multipliers = [0.0]

        transfers = {}
        for cand, mult in zip(to_remove, multipliers):
            valid[ballots == cand] = False
            next_active = _first_nonzero(valid, axis=1)

            o_rows = orig != next_active
            n_rows = o_rows & (next_active != -1)

            weights[n_rows, next_active[n_rows]] = weights[n_rows, orig[n_rows]] * (1 - mult)
            weights[o_rows, orig[o_rows]] *= mult

            transfers_cand = collections.defaultdict(float)
            slicer = n_rows, next_active[n_rows]
            for c, v in zip(ballots[slicer], (weights * votes)[slicer]):
                if v:
                    transfers_cand[int(c)] += float(v)

            if transfers_cand:
                transfers[int(cand)] = dict(transfers_cand)
            orig = next_active

        if not np.isclose(counts.sum(), votes.sum()):  # pragma: no cover
            raise PyRcvError("Final round count total does not equal original votes")

        round_info.append(
            RoundResult(counts.tolist(), list(map(int, elected)), list(map(int, eliminated)), transfers)
        )
    return RaceResult(race_data.metadata, round_info)


def _first_nonzero(arr: ArrayLike, axis: int, invalid_val=-1) -> np.ndarray:
    mask = arr != 0
    return np.where(mask.any(axis=axis), mask.argmax(axis=axis), invalid_val)


def validate_and_standardize_ballots(
    ballots: list[list[int]], num_cands: int
) -> np.ndarray:
    def isiter(lst):
        return isinstance(lst, (list, tuple))

    if not isiter(ballots) or not all(isiter(lst) for lst in ballots):
        raise ValueError("Ballot data are not list of lists")
    if not all(isinstance(el, int) for lst in ballots for el in lst):
        raise ValueError("Ballot data are not all integers")

    ballots_flush = list(itertools.zip_longest(*ballots, fillvalue=0))
    ballots = np.array(ballots_flush, dtype=np.int32).T

    check_oob(ballots, num_cands)
    np.apply_along_axis(check_dup_1d, axis=1, arr=ballots)
    return np.pad(ballots, ((0, 0), (0, 1)))


def check_oob(ballots: ArrayLike, num_cands: int):
    ballots = np.asarray(ballots)
    oob_rankings = (ballots < 0) | (ballots > num_cands)
    if oob_rankings.any():
        bad_ballots = oob_rankings.any(axis=1)
        bad_indices = np.nonzero(bad_ballots)[0].tolist()
        raise ValueError(f"Bad value(s) on ballots: {bad_indices}")


def check_dup_1d(ballot: ArrayLike) -> bool:
    unique, counts = np.unique(ballot, return_counts=True)
    has_dup = (counts[unique != 0] > 1).any()
    if has_dup:
        raise ValueError(f"Ballot has duplicated entry: {ballot.tolist()}")
    return has_dup