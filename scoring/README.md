# scoring/: lead scoring

**On leads the model never saw, A-tier leads won at 3.8x the D-tier rate (10.9% vs 2.9%).**
That headline rests on small counts (11 wins vs 3), so the steadier figure is the top two tiers
against the bottom two: **8.6% vs 3.4%, or 2.5x**. Test AUC is **0.61** (95% CI 0.52–0.69):
better than chance, but a weak signal. Most of what you'd expect to matter (industry, company size,
lead source, replies) showed no measurable link to winning in this data, so the model gives it
zero points instead of pretending.

## What it does

`score.py` gives every lead in `data/sample/leads.csv` a 0–100 score, a tier (A–D) and up to three
plain-English reasons, for example `First response took over a day (-15)`. An average lead scores
50; each reason shows how many points it added or took away.

Output: `output/scores.csv` with `lead_id, score, tier, reason_1, reason_2, reason_3`
(2,000 rows; a reason is blank when fewer than three inputs moved the score), plus
`output/validation.json` with every number on this page.

## Method

1. **Features, measured at the moment a rep would see the score**: 14 days after the lead
   arrived, or the day it became a deal if that came first. Replies that arrive later are
   ignored, so the score can't learn from the outcome it's predicting.
   - Fit: industry, company size, region, source.
   - Behaviour: first-response speed, number of replies, a buying-signal reply ("pricing",
     "demo", "a call"), an opt-out reply, and a reply in the last 7 days.
2. **Target**: the lead ends in a won deal.
3. **Model**: ridge logistic regression (plain numpy). Forward selection on the training period
   adds one input at a time while it lifts cross-validated AUC by at least 0.01. It kept
   **region** and **response speed**; none of the other inputs improved it.
4. **Points**: 1 unit of log-odds = 20 points, centred on the average training lead.

| Input | Points vs an average lead |
|---|---|
| Answered within 15 min / within the hour | +4 / +2 |
| First response took over an hour / over a day | -13 / -15 |
| Never followed up | -23 |
| Region APAC / LATAM / EMEA / North America | +10 / +7 / 0 / -5 |

**Never used as inputs**: `answer_key/`, `replies.true_label`, `leads.status`,
`leads.last_activity_date` (it includes the deal's close date), the owner rep, and anything in
`deals.csv` except the date a deal opened, which is used only to close the observation window.

## Validation

Out of time. Train: leads created before 2026-06-01 (977 leads, 59 won). Test: leads created
June–August (594 leads, 42 won). Leads with an open deal, or created from September on, are left
out because their outcome isn't known yet. They are still scored. Feature choice, the ridge
penalty and the tier cut-offs all come from the training period; the test set was scored once.

| Tier (held-out leads) | Leads | Won | Win rate |
|---|---:|---:|---:|
| A | 101 | 11 | 10.9% |
| B | 317 | 25 | 7.9% |
| C | 71 | 3 | 4.2% |
| D | 105 | 3 | 2.9% |
| All | 594 | 42 | 7.1% |

For comparison, the same model with all nine inputs forced in scored a test AUC of 0.54:
fitting every field mostly fits noise.

## Limits, honestly

- **The signal is weak.** 42 test wins is a small sample, and the AUC interval nearly touches 0.5.
  Treat tiers as a rough ordering, not a forecast.
- **Neither kept input is really lead quality.** Response speed is something the team controls,
  and it overlaps with the slow-response leak elsewhere in this project. Region mostly reflects
  who works the territory: North America's low rate comes from rep R02 (2.6% of leads won vs 5.9%
  for R01). Its points describe the territory as it's run today, not the buyers in it.
- **Tiers are lumpy.** Two inputs give at most 25 possible scores, so tier sizes are uneven
  (1,002 of 2,000 leads are B).
- **Synthetic data.** The generator didn't tie wins to industry or size, so finding no signal there is
  the correct answer here. On a real CRM, rerun the same pipeline and let selection decide.

## Run

From the repo root, with the project `.venv` (pandas and numpy only, no API calls):

```bash
python scoring/score.py
```

It takes about 6 seconds, prints the tier table, and rewrites `scoring/output/`.
