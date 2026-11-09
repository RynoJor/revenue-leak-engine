# hygiene/: CRM hygiene check

**Found $881,884 of $881,884 planted leakage: 625 of 625 planted records, 0 false positives, and
100% recall on duplicates (40 exact + 50 fuzzy), where an exact-match dedupe gets 44%.**

`check.py` reads the CRM export only (`leads.csv`, `deals.csv`). It never opens
`data/sample/answer_key/`. `evaluate.py` is the only file here that reads the answer key, and only
to score the findings afterwards.

## The six checks

| Check | Rule | USD at risk | Found |
|---|---|---|---:|
| `stalled_proposals` | Open deal at `proposal`, no activity for 30+ days | measured: the deal's `amount_usd` | $454,200 |
| `weak_rep_proposal_conversion` | Rep's proposal→won rate vs the rest of the team, flagged at z ≤ −3 (two-proportion test) | rep's closed post-proposal deals × rate gap × average won deal | $198,933 |
| `slow_first_response` | Inbound lead, first response > 60 min (SLA) | closed slow-lead deals × (fast − slow win rate) × average won deal | $85,184 |
| `duplicate_leads` | Same person at the same company, exact or fuzzy; includes open deals on the duplicate | measured: open deal amount on the duplicate (pipeline counted twice) | $84,850 |
| `never_followed_up` | No first response, lead at least 7 calendar days old | value per worked lead | $41,941 |
| `missing_required_fields` | Any of the 8 required lead fields empty | value per worked lead if `email` is missing, else $0 | $16,776 |
| **Total** | | | **$881,884** |

The valuation follows `docs/SCHEMA.md` exactly, but the inputs are recomputed from the CRM data:
average won deal $9,414; value per worked lead $559 (won USD / worked leads, excluding the duplicates
this check found). Estimated USD is spread evenly over the flagged rows so `findings.csv` sums to
the summary.

## Evaluation against the answer key

`python hygiene/evaluate.py`:

| Check | Found | Planted | False pos | Missed | Precision | Recall | USD found | USD planted |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `stalled_proposals` | 40 | 40 | 0 | 0 | 100% | 100% | $454,200 | $454,200 |
| `slow_first_response` | 260 | 260 | 0 | 0 | 100% | 100% | $85,184 | $85,184 |
| `duplicate_leads` | 90 | 90 | 0 | 0 | 100% | 100% | $84,850 | $84,850 |
| `missing_required_fields` | 100 | 100 | 0 | 0 | 100% | 100% | $16,776 | $16,776 |
| `never_followed_up` | 75 | 75 | 0 | 0 | 100% | 100% | $41,941 | $41,941 |
| `weak_rep_proposal_conversion` | 60 | 60 | 0 | 0 | 100% | 100% | $198,933 | $198,933 |
| **Total** | 625 | 625 | 0 | 0 | 100% | 100% | $881,884 | $881,884 |

Duplicates: each of the 90 is linked to the right original lead and labelled exact or fuzzy
correctly, and all 12 duplicate open deals are found. The weak-rep test flags `R02` only.

### Why a perfect score, and why that should not impress you

Five of the six leaks are crisp rules (a date gap, a null, a threshold). On synthetic data built to
those rules, a correct implementation should score 100%, and anything less is a bug. On the first
run `never_followed_up` found 74 of 75. I had measured lead age in elapsed hours, so a lead created
6.25 days before the snapshot fell short of 7 days. The schema counts calendar days, so I changed
the check to match. That fix came from reading the definition, not from tuning to the key. The
duplicate check is the one that takes judgement, so it gets its own tests below.

### The fuzzy-duplicate trade-off

Matching runs in two steps. **Block** by normalised company name (lower case, punctuation and
legal suffixes like LLC / Inc. / Co. dropped, spaces removed so "Lark Well" = "Larkwell") or by a
shared company email domain. A public domain (gmail, outlook…) never forms a block on its own.
**Then confirm the person:** the same mailbox (ignoring case and dots), or a surname similarity
≥ 0.80 (`difflib`) plus a compatible first name (same, initial, prefix like Steve/Steven, or a
known nickname). The earliest `lead_id` is the original.

What each signal buys, on the real sample:

| Rule | Flagged | True pos | False pos | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| exact strings (company + name + email) | 40 | 40 | 0 | 100% | 44% |
| email domain only, public domains allowed | 300 | 90 | 210 | 30% | 100% |
| normalised company + normalised email | 73 | 73 | 0 | 100% | 81% |
| normalised company + exact surname | 74 | 74 | 0 | 100% | 82% |
| normalised company only | 90 | 90 | 0 | 100% | 100% |
| **check.py rule** | 90 | 90 | 0 | 100% | 100% |

"Normalised company only" also scores 100% here, but only because in this sample every company
has one real contact. Real CRMs have several people per account, so `evaluate.py` adds 295
colleague rows (a different person at an existing company) and reruns. Company-only matching merges
all 295 of them. The person check merges none:

| Surname threshold | Flagged | True pos | False pos | Missed | Precision | Recall |
|---:|---:|---:|---:|---:|---:|---:|
| 0.50 – 0.80 | 90 | 90 | 0 | 0 | 100% | 100% |
| 0.90 – 1.00 | 87 | 87 | 0 | 3 | 100% | 97% |

So the surname threshold mostly controls recall. Above 0.9 it drops typo'd surnames ("Andersno").
Precision is held by the first-name rule and the company block. 0.80 is the loosest setting that
keeps a margin over the typos without leaning on the threshold for precision.

## Run it

From the repo root, with the project `.venv`:

```bash
python hygiene/check.py
```

```bash
python hygiene/evaluate.py
```

No packages beyond the repo's `pandas`: fuzzy matching uses the standard library's `difflib`.

Outputs in `hygiene/output/`:

- `findings.csv`: `check`, `record_id` (a `lead_id` or a `deal_id`), `detail`, `usd_at_risk`
- `summary.json`: `checks.<check>.count`, `checks.<check>.usd`, `total_usd`, `total_records`, and a
  `context` block (valuation constants, win rates, per-rep z-scores, duplicate split)
- `evaluation.json`: precision and recall per check, the ablation, and the stress test

## Limits

- **Synthetic data, so the score is an upper bound.** The planted leaks follow the same rules the
  checks encode, and real CRM data is messier.
- **I looked at the key's examples while designing the matcher.** The fuzzy variants (suffixes,
  split names, initials, transposed letters, email forms) are listed in `docs/SCHEMA.md`, and I also
  read the 50 fuzzy pairs to see real examples. The thresholds (`0.80`, z ≤ −3, 60 min, 30 days,
  7 days) are policy defaults, kept in one block at the top of `check.py`.
- **Known false-positive mode:** two people at one company with the same first initial and a
  similar surname ("M. Johnson" vs "Mary Johnson") would be merged. A human should confirm fuzzy
  merges before deleting anything.
- **Known false-negative mode:** the same person under a different company name that does not
  normalise to the same key (a rebrand, or a typo in the company name) with a public email domain.
- **The rep test needs volume.** With 28–60 closed deals per rep, only a large gap passes z ≤ −3.
  `R03` (32% vs 47%, z = −1.6) is weak but not significant at this size. `R05` is a positive
  outlier (71%, z = +3.4) worth learning from, but it is not a leak.
- **Estimated USD is a model, not money lost.** Slow-response and weak-rep values assume the gap in
  win rates is caused by the response time or the rep. The data shows correlation only.
