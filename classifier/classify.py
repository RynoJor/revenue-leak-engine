"""Classify sales-email replies with Claude Haiku, offline, once.

Reads data/sample/replies.csv, drops the ground-truth column before anything else touches the
rows, sends each reply (subject + body) to Claude Haiku with a short system prompt and a strict
JSON schema, and writes classifier/output/labels.csv. The dashboard reads that CSV; it never
calls the API.

Every API result is cached in classifier/output/cache.jsonl, keyed by reply_id and a hash of the
prompt + model, so a rerun only pays for replies it has not seen (or for a changed prompt).

Usage (from the repo root):
    python classifier/classify.py --dry-run            # no API calls: show the prompt and a cost estimate
    python classifier/classify.py --limit 20           # classify the first 20 replies
    python classifier/classify.py                      # classify all 1,500 (cached ones are free)

The API key is read from .env (ANTHROPIC_API_KEY) and is never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPLIES_CSV = ROOT / "data" / "sample" / "replies.csv"
OUT_DIR = ROOT / "classifier" / "output"
CACHE_PATH = OUT_DIR / "cache.jsonl"
LABELS_CSV = OUT_DIR / "labels.csv"

MODEL = "claude-haiku-5-5"
EFFORT = "low"  # classification of a 100-character email does not need deep thinking
MAX_TOKENS = 1024  # room for any thinking plus a ~60-token JSON answer
CONCURRENCY = 8
MAX_ATTEMPTS = 6  # our own retries on top of the SDK's, for parse failures and long rate-limit waits

# Claude Haiku 5.5 list prices, USD per million tokens, for prompts up to 100K tokens.
PRICE_IN_PER_M = 0.10
PRICE_OUT_PER_M = 0.50

LABELS = ["interested", "not_now", "out_of_office", "referral", "unsubscribe"]

# Written from the label definitions a sales team would use, not tuned against the eval set.
SYSTEM_PROMPT = """You triage replies to B2B sales emails for a company that sells invoicing and booking software to small businesses. Classify each reply into exactly one label:

- interested: the sender wants to move forward now: asks for a demo, call, pricing, a trial or next steps, or says yes. Mentioning a colleague who should join the call is still interested.
- not_now: a polite no for now: bad timing, no budget, busy, already using something, "check back later". The door is not closed for good.
- out_of_office: an automatic or personal away message: vacation, leave, sick, travelling, office closed. It says nothing about the offer itself.
- referral: the sender points you to someone else to talk to instead of themselves (they left the company, are not the right person, or forward you to a colleague), usually with a name or email.
- unsubscribe: the sender wants no more email: remove me, stop contacting me, unsubscribe, angry about being emailed.

If a reply mixes signals, pick the label that decides what the rep should do next. Return the label, your confidence from 0 to 1, and a reason of at most 15 words."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["label", "confidence", "reason"],
    "additionalProperties": False,
}

PROMPT_VERSION = hashlib.sha256(
    json.dumps([MODEL, EFFORT, SYSTEM_PROMPT, OUTPUT_SCHEMA], sort_keys=True).encode()
).hexdigest()[:12]


def load_replies() -> pd.DataFrame:
    """Load replies WITHOUT the ground-truth label. The classifier never sees true_label."""
    cols = [c for c in pd.read_csv(REPLIES_CSV, nrows=0).columns if c != "true_label"]
    df = pd.read_csv(REPLIES_CSV, usecols=cols, dtype=str, keep_default_na=False)
    assert "true_label" not in df.columns
    return df


def user_message(row: pd.Series) -> str:
    return f"Subject: {row['subject']}\n\n{row['reply_text']}"


def load_cache() -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("prompt_version") == PROMPT_VERSION:
                    cache[rec["reply_id"]] = rec
    return cache


def load_api_key() -> str | None:
    from dotenv import dotenv_values

    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    key = (dotenv_values(env_path).get("ANTHROPIC_API_KEY") or "").strip()
    return key or None


async def classify_one(client, sem: asyncio.Semaphore, row: pd.Series) -> dict:
    import anthropic

    async with sem:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message(row)}],
                    output_config={
                        "effort": EFFORT,
                        "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                    },
                )
                if resp.stop_reason == "refusal":
                    raise ValueError("model refused")
                text = next(b.text for b in resp.content if b.type == "text")
                out = json.loads(text)
                if out["label"] not in LABELS:
                    raise ValueError(f"unknown label {out['label']!r}")
                return {
                    "reply_id": row["reply_id"],
                    "lead_id": row["lead_id"],
                    "predicted_label": out["label"],
                    "confidence": round(min(max(float(out["confidence"]), 0.0), 1.0), 2),
                    "reason": " ".join(str(out["reason"]).split()),
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "model": MODEL,
                    "prompt_version": PROMPT_VERSION,
                }
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError):
                raise  # retrying will not fix a bad key or model name
            except anthropic.BadRequestError:
                raise
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError,
                    ValueError, KeyError, StopIteration, json.JSONDecodeError) as e:
                if attempt == MAX_ATTEMPTS:
                    raise
                delay = min(2 ** attempt + random.uniform(0, 1), 60)
                print(f"  {row['reply_id']}: {type(e).__name__}, retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.0f}s",
                      file=sys.stderr)
                await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def run(rows: pd.DataFrame, api_key: str) -> tuple[int, int]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=4, timeout=60.0)
    sem = asyncio.Semaphore(CONCURRENCY)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    t0 = time.time()
    tasks = [asyncio.create_task(classify_one(client, sem, r)) for _, r in rows.iterrows()]
    with CACHE_PATH.open("a", encoding="utf-8", newline="\n") as fh:
        for fut in asyncio.as_completed(tasks):
            try:
                rec = await fut
            except Exception as e:  # noqa: BLE001 - one bad reply must not lose the rest
                failed += 1
                print(f"  failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
                if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
                    for t in tasks:
                        t.cancel()
                    print("Authentication failed. Check ANTHROPIC_API_KEY in .env.", file=sys.stderr)
                    break
                continue
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            done += 1
            if done % 100 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} classified ({time.time() - t0:.0f}s)")
    await client.close()
    return done, failed


def write_labels(replies: pd.DataFrame, cache: dict[str, dict]) -> pd.DataFrame:
    recs = [cache[r] for r in replies["reply_id"] if r in cache]
    cols = ["reply_id", "lead_id", "predicted_label", "confidence", "reason"]
    out = pd.DataFrame(recs, columns=cols + ["input_tokens", "output_tokens"])
    out[cols].sort_values("reply_id").to_csv(LABELS_CSV, index=False, lineterminator="\n")
    return out


def usage_cost(cache: dict[str, dict]) -> tuple[int, int, float]:
    tin = sum(r["input_tokens"] for r in cache.values())
    tout = sum(r["output_tokens"] for r in cache.values())
    return tin, tout, tin / 1e6 * PRICE_IN_PER_M + tout / 1e6 * PRICE_OUT_PER_M


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show the prompt and a cost estimate; no API calls")
    ap.add_argument("--limit", type=int, default=None, help="only classify the first N replies")
    args = ap.parse_args()

    replies = load_replies()
    target = replies.head(args.limit) if args.limit else replies
    cache = load_cache()
    todo = target[~target["reply_id"].isin(cache)]
    print(f"{len(target)} replies selected, {len(target) - len(todo)} cached, {len(todo)} to classify "
          f"(model {MODEL}, prompt {PROMPT_VERSION})")

    if args.dry_run:
        # Rough estimate: ~4 characters per token, plus schema/format overhead per request.
        sys_tok = len(SYSTEM_PROMPT) / 4 + 150
        msg_tok = todo.apply(lambda r: len(user_message(r)) / 4, axis=1).sum() if len(todo) else 0
        est_in = len(todo) * sys_tok + msg_tok
        est_out = len(todo) * 120  # JSON answer plus a little thinking
        est = est_in / 1e6 * PRICE_IN_PER_M + est_out / 1e6 * PRICE_OUT_PER_M
        print(f"\nEstimated tokens: ~{est_in:,.0f} in, ~{est_out:,.0f} out -> ~USD {est:.3f}")
        if len(todo):
            print("\n--- system prompt ---\n" + SYSTEM_PROMPT)
            print("\n--- first user message ---\n" + user_message(todo.iloc[0]))
        print("\nDry run: no API calls made.")
        return 0

    if len(todo):
        api_key = load_api_key()
        if not api_key:
            print("\nNo API key found. Create `.env` in the repo root from `.env.example` and paste your "
                  "Anthropic key after `ANTHROPIC_API_KEY=`.", file=sys.stderr)
            return 2
        done, failed = asyncio.run(run(todo, api_key))
        print(f"Classified {done}, failed {failed}.")
        cache = load_cache()

    out = write_labels(replies, cache)
    tin, tout, cost = usage_cost({k: v for k, v in cache.items() if k in set(out["reply_id"])})
    print(f"Wrote {LABELS_CSV.relative_to(ROOT)}: {len(out)} of {len(replies)} replies labelled.")
    print(f"Cumulative API usage for these labels: {tin:,} input + {tout:,} output tokens = USD {cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
