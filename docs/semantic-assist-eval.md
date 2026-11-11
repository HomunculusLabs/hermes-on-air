# Semantic assistance: measured Laya results

What was measured, on what, and what it means for the shadow approval adapter
(`onair/assist.py`). Nothing here promotes Laya to release authority: it stays **off by default
and shadow-only** when enabled.

## Setup

- Service: an existing local Laya 0.3.3 prompt-injection guardrail service (`GET /health`,
  `POST /check`), reached through a loopback SSH forward. It was not changed, restarted or
  reconfigured. `/health` reported model `convaiinnovations/laya`, flag keys `["injection"]`,
  default threshold 0.5, `max_chars` 100000, modes `pre`/`post`.
- Service source hashes (SHA-256): `guardrail_server.py` `72633300c5c752c3…addfb4`,
  `guardrail.py` `e48f9c77b5ca7aa4…5b14da`. Its decision rule: flagged = injection ≥ 0.5 OR
  exfil ≥ 0.9 OR a literal credential regex OR (post mode) a compliance canary. Its notes say
  to treat model-only post-mode flags on ordinary chat-shaped output as review, not block.
- Corpus: `tests/fixtures/assist_eval_corpus.json` (`onair-assist-eval-v1`, 59 hand-written synthetic,
  public-only cases, frozen; SHA-256 `27ea7d36…ebdeb5`). Categories: safe answers 15, unsafe draft
  echoes 12, prompt injection with a safe deflecting draft 12, ambiguity 8, benign lookalikes 12.
  Credential-shaped strings are placeholders expanded to obviously fake values at run time.
- Runner: `tools/assist_eval.py`, which calls the same `AssistRunner.decide_sync` the stage uses.
  Each run made 89 requests (1 health + 88 checks). Two runs, 178 requests in total.

## Results

Run 1 used the first mapping (any draft flag → withhold). Run 2 is the shipped mapping
(deterministic match → withhold; model-only draft flag or flagged chat → review).

| | Run 1 (flag → withhold) | Run 2 (shipped) |
|---|---|---|
| Unsafe echoes suggested **withhold** | 12/12 | 5/12 (all 5 deterministic matches) |
| Unsafe echoes suggested **approve** | 0/12 | 0/12 |
| Safe + lookalike drafts suggested **withhold** (false block) | 8/27 | 0/27 |
| Safe + lookalike drafts suggested approve | 19/27 | 19/27 (8 review) |
| Injection chat, safe draft → review | 6/12 (6 withhold) | 12/12 |
| Ambiguous drafts suggested **approve** | 5/8 | 5/8 |
| Exact match with the corpus label | 37/59 | 39/59 |
| Check latency p50 / max | 32.5 / 883 ms | 31.1 / 122 ms |
| Non-ok statuses | 0 | 0 |

## What this means

- **No unsafe echo was ever suggested for approval** in either run. With the shipped mapping
  the model-flagged echoes surface as review rather than withhold.
- The Laya model flags many short, harmless co-host lines (8 of 27 here, injection score near
  1.0) – the boundary instability its own notes describe. Mapping those flags to withhold would
  falsely block 30% of benign lines, so they map to review.
- Laya judges manipulation and secret exfiltration only. 5 of 8 drafts that need a human
  (risky advice, rumours, a location guess) were suggested **approve**: its approve means only
  "no injection detected", never "fits the show". The desk says so on every recommendation.
- Conclusion: useful as a shadow second opinion on injection echoes and credential leaks; not
  accurate enough to act on. It stays shadow-only; manual approval is unchanged.

## Not measured

- Jev: no key was provisioned, so no Jev request was made. The Jev adapter is covered by fake
  transports only (typed-choice contract, confidence floor, 401, malformed answers).
- Real show traffic, other languages, long drafts, or a different Laya checkpoint.
