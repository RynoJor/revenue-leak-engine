# System

You write a short Monday pipeline brief for a sales leader at a small B2B SaaS company.

Your only source is the JSON facts in the user message. Rules:
- Every number you write must appear in the JSON. You may add a $ sign, thousands separators
  or a % sign, and you may round USD to the nearest thousand (e.g. $454k). Never add, subtract,
  divide or compare numbers yourself. If the JSON has no number for something, say it in words.
- Write numbers as digits, dates as YYYY-MM-DD, and reps as name plus ID, e.g. "Jane Doe (R02)".
- Plain, direct sentences. No hype words, no exclamation marks, no emoji.
- Under 250 words in total.

Use exactly this Markdown layout, with no title line:

**Summary**
Three lines, each its own sentence on its own line.

**Top 3 actions (by $ at risk)**
1. Action, $ amount, Owner: who.
2. ...
3. ...
Take these from actions_ranked_by_usd, in that order.

**Better / worse vs last week**
- Two to four bullets from week_over_week, each saying better or worse.

**Risk**
One sentence.

# User

Facts for the week ending 2026-10-31:

```json
{
  "as_of": "2026-10-31",
  "this_week": {
    "start": "2026-10-25",
    "end": "2026-10-31"
  },
  "prior_week": {
    "start": "2026-10-18",
    "end": "2026-10-24"
  },
  "rules": {
    "stall_days": 30,
    "response_sla_minutes": 60,
    "untouched_days": 7,
    "window_days": 7
  },
  "money_at_risk": {
    "total_usd": 881884,
    "records": 625,
    "by_leak": [
      {
        "leak": "Deals stalled at proposal 30+ days",
        "count": 40,
        "usd": 454200
      },
      {
        "leak": "Rep R02 proposal-to-won gap",
        "count": 60,
        "usd": 198933
      },
      {
        "leak": "Inbound leads answered after 60 minutes",
        "count": 260,
        "usd": 85184
      },
      {
        "leak": "Duplicate leads (pipeline counted twice)",
        "count": 90,
        "usd": 84850
      },
      {
        "leak": "Inbound leads never followed up",
        "count": 75,
        "usd": 41941
      },
      {
        "leak": "Leads missing a required field",
        "count": 100,
        "usd": 16776
      }
    ],
    "new_this_week_usd": 89450,
    "new_prior_week_usd": 23950,
    "note": "new_*_week_usd counts proposals that crossed 30 days idle in that week"
  },
  "new_leaks_this_week": {
    "stalled_proposals_crossed_30_days": {
      "count": 8,
      "usd": 89450
    },
    "slow_inbound_responses": {
      "count": 5,
      "of_inbound_leads": 25
    },
    "new_duplicate_leads": 10,
    "leads_untouched_7_days": 2
  },
  "stalled_proposals": {
    "open_count": 40,
    "open_usd": 454200,
    "oldest_idle_days": 221,
    "by_rep": [
      {
        "rep_id": "R01",
        "rep_name": "Danielle Johnson",
        "count": 11,
        "usd": 114500
      },
      {
        "rep_id": "R05",
        "rep_name": "Robert Johnson",
        "count": 10,
        "usd": 103600
      },
      {
        "rep_id": "R04",
        "rep_name": "Patricia Miller",
        "count": 7,
        "usd": 97750
      },
      {
        "rep_id": "R03",
        "rep_name": "Jill Rhodes",
        "count": 8,
        "usd": 83750
      },
      {
        "rep_id": "R06",
        "rep_name": "Jeffery Wagner",
        "count": 4,
        "usd": 54600
      }
    ]
  },
  "interested_replies_waiting": {
    "count": 89,
    "received_this_week": 5,
    "waiting_over_7_days": 83,
    "top_rep_id": "R01",
    "top_rep_name": "Danielle Johnson",
    "top_rep_count": 28
  },
  "a_tier_leads": {
    "total": 387,
    "created_this_week": 12,
    "not_contacted": 0,
    "with_interested_reply_waiting": 16
  },
  "rep_outlier": {
    "rep_id": "R02",
    "rep_name": "Joshua Walker",
    "proposal_to_won_pct": 18,
    "other_reps_pct": 54,
    "won": 11,
    "closed_after_proposal": 60,
    "z_score": -4.76,
    "estimated_usd_lost": 198933,
    "best_rep_id": "R05",
    "best_rep_name": "Robert Johnson",
    "best_rep_pct": 71,
    "stalled_proposals_owned": 0
  },
  "week_over_week": [
    {
      "metric": "Proposals that crossed 30 days idle",
      "unit": "deals",
      "this_week": 8,
      "prior_week": 1,
      "change": 7,
      "direction": "worse"
    },
    {
      "metric": "USD in proposals that crossed 30 days idle",
      "unit": "USD",
      "this_week": 89450,
      "prior_week": 23950,
      "change": 65500,
      "direction": "worse"
    },
    {
      "metric": "Inbound leads answered after 60 minutes",
      "unit": "leads",
      "this_week": 5,
      "prior_week": 3,
      "change": 2,
      "direction": "worse"
    },
    {
      "metric": "Median inbound first response",
      "unit": "minutes",
      "this_week": 14,
      "prior_week": 13,
      "change": 1,
      "direction": "worse"
    },
    {
      "metric": "New duplicate leads created",
      "unit": "leads",
      "this_week": 10,
      "prior_week": 2,
      "change": 8,
      "direction": "worse"
    },
    {
      "metric": "Inbound leads that hit 7 days untouched",
      "unit": "leads",
      "this_week": 2,
      "prior_week": 0,
      "change": 2,
      "direction": "worse"
    },
    {
      "metric": "New interested replies still waiting",
      "unit": "replies",
      "this_week": 5,
      "prior_week": 6,
      "change": -1,
      "direction": "better"
    },
    {
      "metric": "Deals won",
      "unit": "deals",
      "this_week": 1,
      "prior_week": 7,
      "change": -6,
      "direction": "worse"
    },
    {
      "metric": "USD won",
      "unit": "USD",
      "this_week": 20100,
      "prior_week": 54600,
      "change": -34500,
      "direction": "worse"
    },
    {
      "metric": "New deals opened",
      "unit": "deals",
      "this_week": 19,
      "prior_week": 13,
      "change": 6,
      "direction": "better"
    }
  ],
  "actions_ranked_by_usd": [
    {
      "action": "Call every stalled proposal this week, oldest first (40 deals)",
      "usd": 454200,
      "owner": "Each deal's rep; largest book is Danielle Johnson (R01, 11 deals, $114,500)",
      "rank_by_usd": 1
    },
    {
      "action": "Coach R02 on proposal follow-through; review open proposals with them",
      "usd": 198933,
      "owner": "Sales manager, with Robert Johnson (R05) as peer coach",
      "rank_by_usd": 2
    },
    {
      "action": "Enforce a 60-minute first-response SLA on inbound leads with an alert",
      "usd": 85184,
      "owner": "RevOps (router alert) and the inbound reps",
      "rank_by_usd": 3
    },
    {
      "action": "Merge duplicate leads and close the second open deal on each",
      "usd": 84850,
      "owner": "RevOps",
      "rank_by_usd": 4
    },
    {
      "action": "Work the never-followed-up inbound leads today",
      "usd": 41941,
      "owner": "Lead owners; RevOps to reassign any left after 24 hours",
      "rank_by_usd": 5
    }
  ],
  "actions_without_usd": [
    {
      "action": "Reply to the 89 leads whose last reply said they were interested",
      "usd": null,
      "owner": "Lead owners; most are with Danielle Johnson (R01, 28 leads)"
    }
  ]
}
```

Write the brief.
