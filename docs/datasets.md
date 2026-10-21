# Public datasets

The paper's main results use the simulator, because the simulator gives
**ring-level** ground truth and the shared-attribute links (device, card, IP)
that the hub-aware detector works on. Public datasets are a secondary check.
None of them is committed: everything lives under `data/` (git-ignored).

| Dataset | Labels | Shared attributes? | Source | Status |
|---|---|---|---|---|
| IBM AMLworld HI-Small (Altman et al., NeurIPS 2023 D&B) | per transaction + 370 laundering attempts (groups) | no | Kaggle (free account): `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`, files `HI-Small_Trans.csv`, `HI-Small_Patterns.txt` -> `data/raw/amlworld/` | downloaded by the owner, evaluated |
| Elliptic (Weber et al., 2019) | per transaction node (illicit / licit / unknown) | no | `python scripts/fetch_datasets.py elliptic` (PyTorch Geometric mirror `https://data.pyg.org/datasets/elliptic/`, no account) | downloaded, evaluated |
| IBM AMLSim samples (Suzumura & Kanezashi; github.com/IBM/AMLSim, Apache-2.0) | per account (no group ids) | no | `python scripts/fetch_datasets.py amlsim` (clones the repository, commit `7338a4b`, unpacks `sample/*.tgz`) | downloaded, evaluated |
| Elliptic2 (2024) | subgraph level | no | only by request on elliptic.co (the download link redirects to the home page) | not used |
| PaySim (Lopez-Rojas et al., 2016) | per transaction (account takeover, not rings) | no | Kaggle only (`ealaxi/paysim1`); no official no-account source found | loader only, not used |

## Verified files

Hashes of the files as placed on disk (`python scripts/fetch_datasets.py verify`).

| File | Bytes | sha256 |
|---|---|---|
| `amlworld/HI-Small_Trans.csv` | 475,664,283 | `b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040` |
| `amlworld/HI-Small_Patterns.txt` | 328,161 | `7c5029d7ff0baaf01dba98d9ac2c51c98de288bc37317f4ee5edc44eeef3cb37` |
| `elliptic/elliptic_txs_features.csv.zip` | 150,601,883 | `d33d62159e64b5e889f1a7ea880227c612775b58d409598855e0c4400fa52b3e` |
| `elliptic/elliptic_txs_classes.csv.zip` | 925,698 | `4ca957f0ceffd5dd164e255c7d5ad9ee69a6fa64ae1dd94d6f113e5ebf3b07ba` |
| `elliptic/elliptic_txs_edgelist.csv.zip` | 1,690,631 | `a2f9f6b67a39da2d8cf87fe77b9db89571ba6d880e5dd5b5991dc45c80fa34ec` |
| `amlsim/AMLSim/sample/20K_cycle200.tgz` | 1,280,049 | `da85ffd26f2c0a082f69b038a9bb5ebd2b6e9c0df15707381aeddcfcb572ae20` |
| `amlsim/AMLSim/sample/20K_fanin200.tgz` | 1,285,084 | `e2479ab7a1ecfcd369a7738c855364825561bf9d888d2ad53174e0ed10fd1f1b` |
| `amlsim/AMLSim/sample/20K_fanin200cycle200.tgz` | 1,299,948 | `f187bc5784223cdee137ac603164f7183fd58f114090f39e2a12543b6bf48319` |

Content:
- AMLworld HI-Small: 5,078,345 transactions (5,177 laundering), 2022-09-01 00:00 to 2022-09-18 16:18; 370 attempts (cycle 54, gather-scatter 51, bipartite 49, fan-out 48, scatter-gather 44, stack 43, random 41, fan-in 40; median 8 accounts, median span 75 h).
- Elliptic: 203,769 transaction nodes (4,545 illicit), 234,355 edges, 49 time steps.
- AMLSim samples: 20,000 accounts, about 120,000 transfers over 149 steps; 200 cycles, 200 fan-ins, or both.

Licences: check each dataset's page before redistribution. We redistribute none of them.

## Running

```bash
python -m fraudsim.cli public --dataset amlworld --start-day 0 --days 4 --window-s 3600
python -m fraudsim.cli public --dataset elliptic --window-s 1800
python -m fraudsim.cli public --dataset amlsim --path data/raw/amlsim/samples/20K_cycle200 --window-s 10800
```

Each run is saved to `results/public/runs/<dataset>_<time>.json` and added to
`results/public/results.csv` (dataset, rows, file sha256, slice, window,
account-level precision/recall, attempt recall by typology, wall time).
`make tables` turns the latest run per dataset into `docs/paper/tables/tab4_public.tex`.

## What is and is not comparable

* All three are transaction-only, so the hub-aware shared-attribute detector
  (the paper's main contribution) has nothing to work on. Only the temporal-cycle
  and smurfing detectors run. These results say nothing about hub-aware weighting.
* **AMLworld** attempts are used as ring ground truth: an attempt counts as found
  when one flagged group covers at least half its accounts, and only attempts that
  lie entirely inside the replayed slice count. Attempts span a median of 75 h,
  while the replay window is 1 h (a 1-day window was too slow on the test laptop),
  so attempts whose hops are hours apart cannot be matched.
* **Elliptic** links each transaction to the later transactions that spend its
  outputs, so the graph has no cycles, and amounts are not published (set to 1);
  neither transaction rule can fire. It is reported as not applicable to group
  detection, not as a score.
* **AMLSim samples**: the planted "cycles" are not time-ordered. None of the 21
  cycles we could reconstruct (by equal hop amounts) has its hops in time order
  under any rotation (e.g. hops at steps 28, 113, 84, 59, 78), so a temporal-cycle
  detector, which requires relayed money, cannot match them. The samples also have
  no group ids; the AMLSim generator would provide them but needs networkx 1.11
  and a Java build.
* Published GNN results on these datasets use different splits and
  per-transaction classification metrics; our numbers are not comparable to them.
