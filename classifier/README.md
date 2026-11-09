# classifier/

**99.6% accuracy on 1,500 replies (6 wrong), USD 0.14 total, about 4.5 minutes.** Claude Haiku 5.5
sorts sales-email replies into five buckets, so reps can act on the right ones. All 441 replies
labelled `interested` were caught.

Keep in mind that this is synthetic data built from a fairly small set of sentence templates. Read
[What it gets wrong, and why the number is flattering](#what-it-gets-wrong-and-why-the-number-is-flattering)
before quoting the 99.6%.

## How it works

1. `classify.py` loads `data/sample/replies.csv` and **drops `true_label` before any row is
   used**, so the model never sees the answer. Only `evaluate.py` reads that column.
2. Each reply goes to `claude-haiku-5-5` as `Subject: ...` plus the body. The system prompt is a
   short one with a plain-English definition of each label. It was written from how a sales team
   would describe the buckets, not tuned against this eval set.
3. The output is forced into a strict JSON schema (`output_config.format`): `label` (an enum
   of the five labels), `confidence` (0 to 1) and `reason` (at most 15 words). There's no free
   text to parse and no invalid labels. Effort is set to `low`, which is enough for a
   100-character email.
4. Eight requests run at a time. The SDK retries 429s and 5xx errors, and the script adds its own
   backoff retries for anything that still fails. A failed reply is logged and skipped, so it
   can't sink the run.
5. Every result is appended to `output/cache.jsonl`, keyed by `reply_id` and a hash of the model,
   effort, prompt and schema. A rerun with the same prompt costs nothing. Changing the prompt
   re-classifies everything under a new hash.
6. `output/labels.csv` is written from the cache. **The dashboard reads this file and never
   calls the API**, so a visitor costs USD 0.

The labels are `interested`, `not_now`, `out_of_office`, `referral` and `unsubscribe`.

## Eval

`python classifier/evaluate.py` writes [`output/eval.md`](output/eval.md) and
`output/eval.json`.

| Label | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `interested` | 441 | 98.7% | 100.0% | 0.993 |
| `not_now` | 400 | 100.0% | 98.5% | 0.992 |
| `out_of_office` | 312 | 100.0% | 100.0% | 1.000 |
| `referral` | 142 | 100.0% | 100.0% | 1.000 |
| `unsubscribe` | 205 | 100.0% | 100.0% | 1.000 |

Macro F1 is 0.997. The confusion matrix has exactly one off-diagonal cell: 6 `not_now` replies
predicted as `interested`.

The confidence scores carry real signal. Replies the model rated 0.95 or higher (1,001 of them)
were 100% correct. Replies rated 0.70 to 0.89 (170 of them) were 97.1% correct, and every error
sits in that band or just above it. A sensible production rule would send anything under 0.90 to
a human for a glance.

## What it gets wrong, and why the number is flattering

**The six errors are all one sentence:** *"We're reviewing tools next quarter. Put me down for a
follow-up then."* The data generator calls this `not_now`. The model reads "put me down for a
follow-up" as a request for next steps and calls it `interested`, with confidence 0.80 to 0.90.
Honestly, that's a borderline call: a human SDR might well file it as a warm lead. The fix is a
line in the prompt ("a request to follow up at a later date is `not_now`"). I didn't add it,
because tuning the prompt on the eval set and then reporting the eval number would be grading my
own homework.

From a revenue point of view, this is the safe direction to be wrong. Nothing marked
`interested` in the ground truth was missed. The cost of the error is six replies that a rep
opens a quarter early.

**Why 99.6% will not hold on real email.** The 1,500 replies are synthetic. Each is a greeting,
one core sentence and a sign-off, and the core sentences repeat: about 300 distinct sentences in
total, and only around a dozen for `not_now`. Once the model gets a template right, it gets every
copy right, which is also why all six errors are the same sentence. Real replies are longer,
quote earlier threads, mix intents ("not now, but talk to Sam") and include sarcasm. Expect a
real inbox to score lower, and build a hand-labelled sample of real replies before trusting any
number. The pipeline (schema, cache, eval, confidence threshold) carries over unchanged; only the
headline number doesn't.

## How to run it

From the repo root, using the project's `.venv`:

```bash
cp .env.example .env          # then paste your key after ANTHROPIC_API_KEY=
python classifier/classify.py --dry-run          # no API calls: shows the prompt and a cost estimate
python classifier/classify.py --limit 20         # smoke test on 20 replies (about USD 0.002)
python classifier/classify.py                    # all 1,500; cached replies are free
python classifier/evaluate.py                    # writes output/eval.md and output/eval.json
```

The key is read from `.env`, which is git-ignored, and is never printed or logged.

## Cost

| | Tokens | USD |
|---|---:|---:|
| Input | 1,049,289 | 0.105 |
| Output | 76,648 | 0.038 |
| **Total for 1,500 replies** | | **0.143** |

That's about USD 0.0001 per reply at Haiku 5.5 list prices ($0.10 / $0.50 per million tokens).
Most of the input is the system prompt and schema repeated on every call, about 700 tokens per
request. The dry-run estimate was USD 0.16. Reruns cost USD 0 thanks to the cache.

## Files

| File | What it is |
|---|---|
| `classify.py` | Classifier: prompt, schema, concurrency, retries, cache, `--dry-run`, `--limit` |
| `evaluate.py` | Scores predictions against `true_label` |
| `output/labels.csv` | **Read by the dashboard.** `reply_id, lead_id, predicted_label, confidence, reason` |
| `output/cache.jsonl` | Raw results with token usage, keyed by reply and prompt version |
| `output/eval.md`, `output/eval.json` | The eval report |
