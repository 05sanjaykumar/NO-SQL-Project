# Public datasets

The paper's main results use the simulator, because the simulator gives
**ring-level** ground truth and the shared-attribute links (device, card, IP)
that the hub-aware detector works on. Public datasets are used as a secondary
check. None of them is committed: everything lives under `data/` (git-ignored).

| Dataset | Labels | Shared attributes? | How to get it | Status |
|---|---|---|---|---|
| IBM AMLworld HI-Small (Altman et al., NeurIPS 2023 D&B) | per transaction + 370 laundering attempts (groups) | no | Kaggle (free account): `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`, files `HI-Small_Trans.csv`, `HI-Small_Patterns.txt` -> `data/raw/amlworld/` | downloaded by the user, verified below |
| Elliptic (Weber et al., 2019) | per transaction node (illicit / licit / unknown) | no | `python scripts/fetch_datasets.py elliptic` (PyTorch Geometric mirror, `https://data.pyg.org/datasets/elliptic/`, no account) | not downloaded yet |
| IBM AMLSim (Suzumura & Kanezashi, Apache-2.0) | alert groups by typology (fan-in, fan-out, cycle, ...) | no | `python scripts/fetch_datasets.py amlsim`, then build and run the simulator (Java 8+, Maven) | not generated yet |
| Elliptic2 (2024) | subgraph level | no | needs a request on elliptic.co (no direct download) | not used |
| PaySim (Lopez-Rojas et al., 2016) | per transaction (account takeover, not rings) | no | Kaggle (free account): `ealaxi/paysim1` -> `data/raw/paysim/` | not used |

## Verified files

Hashes are of the files as placed on disk (`python scripts/fetch_datasets.py verify`).

| File | Bytes | sha256 | Content |
|---|---|---|---|
| `data/raw/amlworld/HI-Small_Trans.csv` | 475,664,283 | `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040` | 5,078,345 transactions, 5,177 laundering, 2022-09-01 00:00 to 2022-09-18 16:18 |
| `data/raw/amlworld/HI-Small_Patterns.txt` | 328,161 | `7c5029d7ff0baaf01dba98d9ac2c51c98de288bc37317f4ee5edc44eeef3cb37` | 370 attempts: cycle 54, gather-scatter 51, bipartite 49, fan-out 48, scatter-gather 44, stack 43, random 41, fan-in 40 (median 8 accounts, max 45) |

Licences: check each dataset's page before redistribution. We redistribute none of them.

## Running

```bash
python -m fraudsim.cli public --dataset amlworld            # reads data/raw/amlworld/
python -m fraudsim.cli public --dataset amlworld --max-rows 500000
python -m fraudsim.cli public --dataset elliptic
```

Each run appends one row to `results/public/results.csv` (dataset, rows read,
file sha256, account-level precision/recall/F1, group recall by typology,
latency).

## What is and is not comparable

* All three datasets are transaction-only, so the hub-aware shared-attribute
  detector (the paper's main contribution) has nothing to work on. Only the
  temporal-cycle and smurfing detectors run. These results say nothing about
  hub-aware weighting.
* AMLworld laundering attempts are used as ring ground truth: an attempt counts
  as found when one flagged group covers at least half its accounts. Our
  ring definition (a coordinated group) is close to, but not the same as, an
  AMLworld attempt (a set of transactions following one typology).
* Elliptic labels transactions, not groups, so only account(node)-level
  precision/recall is reported, and amounts are unknown (set to 1).
* Published GNN results on these datasets use different splits and per-
  transaction classification metrics; our numbers are not comparable to them.
