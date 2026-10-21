"""
Public-dataset loaders, tested on tiny files in each dataset's published format.
The rows below are made up for the test; they are not taken from the datasets.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from fraudsim.datasets import load_dataset, run_public
from fraudsim.datasets.base import evaluate_accounts
from fraudsim.types import EventType

AML_HEADER = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
    "Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
)


def _aml(tmp: Path) -> Path:
    rows = []
    # A laundering cycle A -> B -> C -> A, repeated, plus benign traffic.
    for k in range(4):
        m = 10 + k * 3
        rows += [
            f"2022/09/01 00:{m:02d},001,A,002,B,900.00,US Dollar,900.00,US Dollar,ACH,1",
            f"2022/09/01 00:{m + 1:02d},002,B,003,C,880.00,US Dollar,880.00,US Dollar,ACH,1",
            f"2022/09/01 00:{m + 2:02d},003,C,001,A,860.00,US Dollar,860.00,US Dollar,ACH,1",
        ]
    rows += [
        f"2022/09/01 00:{k:02d},010,X{k},011,Y{k},50.00,Euro,50.00,Euro,Cash,0" for k in range(10)
    ]
    (tmp / "T-Small_Trans.csv").write_text(AML_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    (tmp / "T-Small_Patterns.txt").write_text(
        "BEGIN LAUNDERING ATTEMPT - CYCLE:  Max 3 hops\n"
        + "\n".join(rows[:3])
        + "\nEND LAUNDERING ATTEMPT - CYCLE\n",
        encoding="utf-8",
    )
    return tmp


def test_amlworld_loader_and_replay(tmp_path: Path):
    ds = load_dataset("amlworld", str(_aml(tmp_path)))
    events = list(ds.events)
    assert len(events) == len(ds.events)
    assert ds.illicit_accounts == {"001:A", "002:B", "003:C"}
    assert [(g["type"], g["members"]) for g in ds.groups] == [
        ("cycle", ["001:A", "002:B", "003:C"])
    ]
    # A slice that ends before the attempt's last transaction drops it from the truth.
    early = load_dataset("amlworld", str(tmp_path), days=12 / 1440)
    assert early.groups == []
    assert early.source["rows"] < 22
    txns = [e for e in events if e.event_type == EventType.TRANSACTION]
    assert len(txns) == 22
    assert all(a.ts <= b.ts for a, b in itertools.pairwise(events))
    # Every account joins before its first transaction.
    joined: set[str] = set()
    for e in events:
        if e.event_type == EventType.USER_JOIN:
            joined.add(e.payload["user_id"])
        else:
            assert {e.payload["sender"], e.payload["receiver"]} <= joined
    res = run_public(ds, batch_size=3)
    assert res["events"] == len(events)
    assert res["group_recall"] == 1.0  # the cycle detector finds A -> B -> C -> A
    assert res["account_precision"] == 1.0


def test_elliptic_loader(tmp_path: Path):
    (tmp_path / "elliptic_txs_features.csv").write_text(
        "1,1,0.1\n2,1,0.2\n3,2,0.3\n", encoding="utf-8"
    )
    (tmp_path / "elliptic_txs_classes.csv").write_text(
        "txId,class\n1,1\n2,2\n3,unknown\n", encoding="utf-8"
    )
    (tmp_path / "elliptic_txs_edgelist.csv").write_text("txId1,txId2\n1,2\n2,3\n", encoding="utf-8")
    ds = load_dataset("elliptic", str(tmp_path))
    assert ds.illicit_accounts == {"tx1"}
    assert ds.accounts == {"tx1", "tx2", "tx3"}
    assert sum(e.event_type == EventType.TRANSACTION for e in ds.events) == 2


def test_paysim_loader(tmp_path: Path):
    header = (
        "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,"
        "newbalanceDest,isFraud,isFlaggedFraud\n"
    )
    rows = [
        "1,PAYMENT,10.0,C1,100,90,M1,0,0,0,0",
        "1,TRANSFER,500.0,C2,500,0,C3,0,500,1,0",
        "2,CASH_OUT,500.0,C3,500,0,C4,0,0,1,0",
    ]
    (tmp_path / "PS_test_log.csv").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    ds = load_dataset("paysim", str(tmp_path))
    assert ds.illicit_accounts == {"C2", "C3", "C4"}
    txns = [e for e in ds.events if e.event_type == EventType.TRANSACTION]
    assert [t.payload["amount"] for t in txns] == [10.0, 500.0, 500.0]
    assert txns[0].ts < txns[1].ts < txns[2].ts


def test_account_evaluation_counts(tmp_path: Path):
    ds = load_dataset("amlworld", str(_aml(tmp_path)))
    out = evaluate_accounts(ds, [{"001:A", "002:B", "010:X1"}])
    assert out["illicit_flagged"] == 2
    assert out["account_precision"] == 2 / 3
    assert out["account_recall"] == 2 / 3
    assert out["group_recall"] == 1.0
