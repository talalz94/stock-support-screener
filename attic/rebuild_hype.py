"""Recompute the hype score series now that short volume is an input.

The stored 118 sessions were computed BEFORE FINRA short volume existed, so
they carry 9 of the 11 composite members. Leaving them would make the series a
mix of two different metric sets, and factor_lab would be measuring a moving
definition rather than a signal. `rebuild=True` overwrites rather than skips.

dip is rebuilt afterwards because its `not_extended` leg reads hype's output.
"""
import sys

import config

config.safe_console()

import scores
import scores.dip      # noqa: F401
import scores.hype     # noqa: F401

if __name__ == "__main__":
    scores.load_all()
    print("=== hype rebuild (short volume now included) ===", flush=True)
    scores.catchup("hype", every=21, frm="2016-08-01", rebuild=True)
    print("=== dip rebuild (reads hype) ===", flush=True)
    scores.catchup("dip", every=21, frm="2022-12-01", rebuild=True)
    print("DONE", flush=True)
    sys.exit(0)
