# Data schema and answer key

Every number other modules are checked against lives here. The data is fully synthetic: Faker
names, invented company names, `.example` and invented `.com` domains, USD amounts. It describes a
fictional B2B SaaS company that sells invoicing and booking software to small businesses.

- **Generator:** `data/generate.py` (seed `42`, snapshot date `2026-10-31`). Run
  `python data/generate.py` from the repo root to rebuild `data/sample/` byte for byte. It also
  rewrites the leak table below, so this file cannot fall out of step with the data.
- **Reproducibility:** Faker is pinned in `requirements.txt` because its output changes between
  versions.
- **"Today":** every age calculation uses the snapshot date **2026-10-31**, never the real clock.
  Use `AS_OF = date(2026, 10, 31)` in every module.

## Files

All in `data/sample/`.

| File | Rows | One row is |
|---|---:|---|
| `leads.csv` | 2,000 | a lead (a contact at a small business), including planted duplicates |
| `deals.csv` | 600 | an opportunity opened from a lead |
| `deal_stage_history.csv` | one per stage entered | a deal entering a stage (event log) |
| `replies.csv` | 1,500 | an email reply from a lead |
| `reps.csv` | 6 | a sales rep |
| `answer_key/leak_summary.json` | | the leak counts and USD values below, as JSON |
| `answer_key/planted_records.csv` | | every record that was planted as a leak |
| `answer_key/duplicate_pairs.csv` | 90 | each duplicate lead and the original it duplicates |

**`answer_key/` is for tests and evals only.** No module may read it to make its own decisions.
The same goes for `replies.csv` → `true_label`: the classifier must drop that column before it
sees a row. Only the eval reads it.

Formats: datetimes are `YYYY-MM-DD HH:MM:SS` (no timezone), dates are `YYYY-MM-DD`, empty means
null. Booleans are `True` / `False`. IDs are zero-padded strings.

### `reps.csv`

| Field | Type | Meaning |
|---|---|---|
| `rep_id` | str `R01`–`R06` | Rep key, used as `owner_rep_id` elsewhere |
| `rep_name` | str | Faker name |
| `region` | str | Territory the rep covers |
| `email` | str | Work email on the fictional `ledgerloop.example` domain |

Territories: North America `R01`, `R02`; EMEA `R03`, `R04`; APAC `R05`; LATAM `R06`. A lead is
owned by a rep from its region.

### `leads.csv`

| Field | Type | Meaning |
|---|---|---|
| `lead_id` | str `L00001`… | Lead key. `L00001`–`L01910` are original leads; `L01911`–`L02000` are the planted duplicates |
| `company` | str, required | Business name |
| `contact_name` | str, required | Person at the business |
| `email` | str, required | Contact email. About 12% use a public mail domain (gmail.com, yahoo.com, outlook.com, hotmail.com, icloud.com); the rest use the company domain. **Never match duplicates on a public domain alone** |
| `industry` | str, required | One of: Retail, Food & Beverage, Professional Services, Health & Wellness, Fitness, Beauty & Personal Care, Real Estate, Education, Automotive, Hospitality |
| `company_size` | str, required | Employee band: `1-10`, `11-50`, `51-200` |
| `region` | str, required | `North America`, `EMEA`, `APAC`, `LATAM` |
| `source` | str, required | Inbound: `website_form`, `demo_request`, `free_trial`, `content_download`. Outbound: `outbound_email`, `referral`, `event`, `partner` |
| `channel` | str | `inbound` or `outbound`, derived from `source` |
| `created_at` | datetime | When the lead entered the CRM |
| `owner_rep_id` | str, required | Assigned rep (`reps.rep_id`) |
| `first_response_at` | datetime, nullable | First rep touch. Inbound: the reply to the enquiry. Outbound: the first outreach. Null = never followed up |
| `first_response_minutes` | int, nullable | Minutes from `created_at` to `first_response_at`. **Speed-to-lead analysis uses inbound leads only** |
| `last_activity_date` | date, nullable | Latest of first response, last reply received and the deal's last activity |
| `status` | str | `new` (never touched), `contacted`, `nurturing`, `unqualified`, `unsubscribed` (sent an unsubscribe reply), `converted` (has a deal) |

Required fields (for the hygiene check): `company`, `contact_name`, `email`, `industry`,
`company_size`, `region`, `source`, `owner_rep_id`.

### `deals.csv`

Stages run lead → qualified → demo → proposal → negotiation → won / lost. The lead stage lives in
`leads.csv`; a deal row exists from `qualified` onward.

| Field | Type | Meaning |
|---|---|---|
| `deal_id` | str `D0001`… | Deal key |
| `lead_id` | str | The lead it came from (`leads.lead_id`), one deal per lead |
| `company` | str | Copied from the lead at the time the deal was opened |
| `owner_rep_id` | str | Rep who owns the deal |
| `region` | str | Lead's region |
| `amount_usd` | int | Annual contract value in USD |
| `stage` | str | Current stage: `qualified`, `demo`, `proposal`, `negotiation`, `won`, `lost` |
| `is_closed` | bool | `stage` is `won` or `lost` |
| `is_won` | bool | `stage` is `won` |
| `qualified_date` | date | Entered qualified (= deal created) |
| `demo_date` | date, nullable | Entered demo |
| `proposal_date` | date, nullable | Entered proposal |
| `negotiation_date` | date, nullable | Entered negotiation |
| `closed_date` | date, nullable | Won or lost date |
| `last_activity_date` | date | Last logged activity. Equals `closed_date` on closed deals |
| `lost_reason` | str, nullable | Why it was lost |
| `lost_at_stage` | str, nullable | Stage the deal was in when lost |

A stage date is filled only if the deal reached that stage. Open deals have no `closed_date`.

### `deal_stage_history.csv`

The same stage dates in long form, one row per stage a deal entered, including the terminal
`won` / `lost` row. Use it for funnels and time-in-stage.

| Field | Type | Meaning |
|---|---|---|
| `deal_id` | str | `deals.deal_id` |
| `stage` | str | Stage entered |
| `entered_date` | date | Date it entered that stage |

### `replies.csv`

| Field | Type | Meaning |
|---|---|---|
| `reply_id` | str `E00001`… | Reply key, ordered by `received_at` |
| `lead_id` | str | Lead who replied. A lead can reply more than once |
| `from_email` | str | Sender address (the lead's email) |
| `received_at` | datetime | When it arrived, always after the lead's first response |
| `subject` | str | Subject line |
| `reply_text` | str | The reply body, multi-line, quoted in the CSV |
| `true_label` | str | **Hidden from the classifier.** One of `interested`, `not_now`, `out_of_office`, `referral`, `unsubscribe` |

Label guide for the eval:

- `interested`: wants pricing, a demo, a trial or a call.
- `not_now`: open to it later, or not this year; not a hard no.
- `out_of_office`: an automatic or away reply, even when it names a colleague to contact.
- `referral`: a person sends you to someone else at the business.
- `unsubscribe`: asks to stop or be removed, including angry replies.

## Planted leaks: the answer key

Each leak has an exact definition. The generator measures it from the finished data with that
definition and checks the result against the records it planted. Lead-level leaks are disjoint:
no lead is planted in two of them.

"Measured" USD is a sum of real deal amounts. "Estimated" USD is a modelled loss, and its formula
is shown. Two shared constants: **average won amount** (mean `amount_usd` of won deals) and
**value per worked lead** (won USD / leads with a first response, duplicates excluded).

Definitions:

1. **Stalled proposals:** `deals.stage == 'proposal'` and `2026-10-31 - last_activity_date >= 30`
   days. Every other open deal has activity within the last 20 days, so this is the only stall in
   the data.
2. **Slow first response:** `leads.channel == 'inbound'` and `first_response_minutes > 60`. Fast
   inbound responses are 1–45 minutes; slow ones are 4 hours to 3 days. Win rates are compared on
   closed deals from inbound leads.
3. **Duplicate leads:** a later row for the same person at the same company. *Exact* means
   `company`, `contact_name` and `email` are identical strings. *Fuzzy* means the same person
   written differently: case changes, a legal suffix added or dropped ("LLC", ", Inc."), a split
   compound name ("Lark Well" for "Larkwell"), a nickname or initial ("Mike", "N. Galloway"), a
   transposed letter, or an email variant (upper case, dots removed, `first-initial+last`). The
   original is always the lower `lead_id`. Twelve duplicates also carry their own open deal,
   which double-counts that pipeline.
4. **Missing required fields:** any required lead field is empty. Each affected lead is missing
   exactly one field.
5. **Never followed up:** `leads.first_response_at` is empty. All are inbound and at least 7 days
   old at the snapshot.
6. **Weak rep:** proposal-to-won rate per rep, on closed deals that reached proposal
   (`proposal_date` filled and `is_closed`). `R02` is planted as the weak rep.

Planted record IDs: `answer_key/planted_records.csv` (`leak_id`, `record_type`, `record_id`,
`note`). The weak-rep leak has no record list; it is the rate in the table.

<!-- LEAKS:START -->
_Generated by `data/generate.py` (seed 42, snapshot 2026-10-31). Do not edit by hand._

Rows: **2,000 leads**, **600 deals**, **1,500 replies**, 6 reps.

| # | Leak | Entity | Count | USD | Basis | How USD is worked out |
|---|---|---|---:|---:|---|---|
| 1 | Deals stalled at proposal (`stalled_proposals`) | deals | 40 | $454,200 | measured | sum(amount_usd) of those deals (pipeline at risk) |
| 2 | Slow first response on inbound leads (`slow_first_response`) | leads | 260 | $85,184 | estimated | closed slow-lead deals x (fast win rate - slow win rate) x average won amount |
| 3 | Duplicate leads (exact + fuzzy) (`duplicate_leads`) | leads | 90 | $84,850 | measured | sum(amount_usd) of open deals opened on a duplicate lead (pipeline counted twice) |
| 4 | Leads missing a required field (`missing_required_fields`) | leads | 100 | $16,776 | estimated | leads missing email (cannot be worked) x value per worked lead; other fields are a routing/reporting cost, valued at $0 |
| 5 | Leads never followed up (`never_followed_up`) | leads | 75 | $41,941 | estimated | count x value per worked lead |
| 6 | Rep R02 proposal-to-won rate (`weak_rep_proposal_conversion`) | deals | 60 | $198,933 | estimated | rep's closed post-proposal deals x (other reps' rate - rep's rate) x average won amount |
| | **Total** | | | **$881,884** | | |

Supporting numbers:

- Average won deal: $9,414. Value per worked lead (won USD / leads with a first response, duplicates excluded): $559.
- Won: 109 deals, $1,026,150. Open pipeline: $1,005,250.
- Slow vs fast inbound win rate (closed deals): 10% (6/61) vs 25% (56/227); 9.05 excess lost deals.
- Duplicates: 40 exact, 50 fuzzy; 12 of them carry a second open deal.
- Missing fields: email 30, industry 25, company_size 25, region 20 (one field per lead).
- Proposal-to-won by rep (closed deals that reached proposal): R01 56% (27/48), R02 18% (11/60), R03 32% (11/34), R04 47% (18/38), R05 71% (25/35), R06 61% (17/28). R02 vs everyone else: 18% vs 54%.
- Reply labels: interested 441, not_now 400, out_of_office 312, referral 142, unsubscribe 205.
<!-- LEAKS:END -->
