"""
IBM Transactions for Anti-Money Laundering ("AMLworld", Altman et al., NeurIPS 2023
Datasets & Benchmarks).

Download (free Kaggle account required):
    https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml
Put e.g. HI-Small_Trans.csv and HI-Small_Patterns.txt in one folder and pass that
folder (or the *_Trans.csv path) to load_amlworld().

Trans.csv columns (by position): Timestamp, From Bank, Account, To Bank, Account,
Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format,
Is Laundering. An account is identified by bank + account number. Amounts are kept
in their payment currency (the detectors only use relative amounts).

Patterns.txt lists laundering attempts as blocks
    BEGIN LAUNDERING ATTEMPT - <TYPE>[: details]
    <transaction rows in the Trans.csv format>
    END LAUNDERING ATTEMPT - <TYPE>
which we load as labelled groups (fan-in, fan-out, cycle, scatter-gather, ...).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fraudsim.datasets.base import LazyEvents, PublicDataset, file_digest

TS_FORMAT = "%Y/%m/%d %H:%M"


def _acc(bank: str, account: str) -> str:
    return f"{bank.strip()}:{account.strip()}"


def _files(path: str | Path) -> tuple[Path, Path | None]:
    p = Path(path)
    if p.is_dir():
        trans = sorted(p.glob("*_Trans.csv"))
        if not trans:
            raise FileNotFoundError(f"no *_Trans.csv in {p} (see module docstring for download)")
        p = trans[0]
    patterns = p.with_name(p.name.replace("_Trans.csv", "_Patterns.txt"))
    return p, patterns if patterns.exists() else None


def parse_patterns(path: Path) -> list[dict]:
    groups: list[dict] = []
    current: dict | None = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                kind = line.split("-", 1)[1].split(":", 1)[0].strip().lower().replace(" ", "_")
                current = {"id": f"attempt_{len(groups)}", "type": kind, "members": set(), "ts": []}
            elif line.startswith("END LAUNDERING ATTEMPT"):
                if current is not None:
                    current["members"] = sorted(current["members"])
                    current["first"], current["last"] = min(current["ts"]), max(current["ts"])
                    del current["ts"]
                    groups.append(current)
                current = None
            elif current is not None and line:
                row = next(csv.reader([line]))
                current["members"].update({_acc(row[1], row[2]), _acc(row[3], row[4])})
                current["ts"].append(datetime.strptime(row[0], TS_FORMAT))
    return groups


def load_amlworld(
    path: str | Path,
    max_rows: int | None = None,
    start_day: float = 0.0,
    days: float | None = None,
) -> PublicDataset:
    """
    Load transactions, optionally a contiguous time slice [start_day, start_day + days)
    counted from midnight of the file's first date. Laundering attempts are kept as ring
    ground truth only when all of their transactions fall inside the slice.
    """
    trans, patterns = _files(path)
    illicit: set[str] = set()
    # Compact rows (offset_s, txn_id, sender, receiver, amount); account ids interned.
    # The slice is counted from midnight of the first row's date, and rows are
    # filtered while reading, so a slice never holds the whole file in memory.
    raw: list[tuple[datetime, int, str, str, float]] = []
    lo = hi = None
    with open(trans, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for i, row in enumerate(reader):
            if max_rows is not None and i >= max_rows:
                break
            ts = datetime.strptime(row[0], TS_FORMAT)
            if lo is None:
                lo = datetime(ts.year, ts.month, ts.day) + timedelta(days=start_day)
                hi = lo + timedelta(days=days) if days is not None else None
            if ts < lo or (hi is not None and ts >= hi):
                continue
            sender = sys.intern(_acc(row[1], row[2]))
            receiver = sys.intern(_acc(row[3], row[4]))
            raw.append((ts, i, sender, receiver, float(row[7])))
            if row[10].strip() == "1" and sender != receiver:
                illicit.update((sender, receiver))
    if lo is None:
        raise ValueError(f"{trans} has no rows")
    raw.sort(key=lambda r: (r[0], r[1]))  # the file is not strictly time-ordered
    rows = [((ts - lo).total_seconds(), f"aml{i}", s_, r_, amt) for ts, i, s_, r_, amt in raw]
    del raw
    events = LazyEvents("amlworld", rows)
    groups = parse_patterns(patterns) if patterns else []
    groups = [
        g
        for g in groups
        if g["first"] >= lo
        and (hi is None or g["last"] < hi)
        and set(g["members"]) <= events.accounts
    ]
    for g in groups:
        g["first"], g["last"] = g["first"].isoformat(), g["last"].isoformat()
    return PublicDataset(
        "amlworld",
        events,
        illicit,
        events.accounts,
        groups,
        {
            "file": trans.name,
            "sha256": file_digest(str(trans)),
            "rows": len(rows),
            "slice": f"{lo.isoformat()} .. {hi.isoformat() if hi else 'end'}",
            "patterns_file": patterns.name if patterns else None,
        },
    )
