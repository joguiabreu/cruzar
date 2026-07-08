# Classifying a whole statement with a small-context model

> A learning-oriented walkthrough of how `cruzar anonymize` decides which tokens in a statement
> are personal data, using a *local* model whose context window is far too small to see the job
> all at once. The punchline surprises people twice: the context window is a **shared** budget for
> input *and* output, and the fix that made a tiny 3B model produce clean results was **architecture,
> not a bigger model**. Companion to `plan_030_document_anonymizer.md`.

## The problem

The anonymizer needs, for a statement, to know which tokens are structural **labels** to keep
(`Data`, `Saldo`, `DESCRICAO`, a store's name) and which are real **data** to replace (an amount,
a person's name, a NIF). A dense document is bigger than it looks: a one-page supermarket receipt
has **~300 distinct tokens**.

The obvious approach — one model call, "here's the statement and all 300 tokens, label them all" —
is exactly what blew up on the first real run:

```text
LlmError: token classification failed: The output is incomplete due to a max_tokens length limit.
```

To see why, you have to separate two things that are both called "token."

## Two very different "tokens"

| Aspect | Document token | LLM token |
| --- | --- | --- |
| What it is | one `pdfplumber` word: `9,99`, `COMPRA`, `PT999999999` | the model's unit of text: ~4 chars, ~¾ of a word |
| Who counts them | us (the anonymizer) | the model's context window (`4096`) |
| The receipt | ~300 of them | thousands, once you write it all out |

"300 tokens" sounds tiny — it's ~300 *words*. But the model budgets in **LLM tokens**, and what
matters is how much text passes **through the window**.

## The window is input **+** output, not two limits

The idea people miss: a model's context window (here Ollama's `num_ctx`, `4096`) holds the prompt
**and** everything it generates, **together**. They compete for the same space.

```text
        ┌──────────────── 4096-token window ────────────────┐
        │  prompt (system + statement + token list)         │  generated JSON  │
        │  ~3000 tokens ──────────────────────────────────▶ │ ◀── only ~1000 left │
        └───────────────────────────────────────────────────┘
                                                   ▲
                       a label for all 300 tokens needed ~5400 — didn't fit, truncated here
```

The output did the damage. One JSON entry per token —
`{"index":5,"kind":"replace","type":"amount"}` — is **~18 LLM tokens**. For 300 tokens:

```text
prompt        ≈ 3,000 tokens   (instructions + full statement text + the 300-item listing)
output        ≈ 300 × 18 = 5,400 tokens   (a label per token)
needed        ≈ 8,400 tokens
available     =  4,096 tokens        ← prompt + output must BOTH fit here
```

It truncated mid-list. Nothing was wrong with the model; we asked it to write more than the window
could hold.

## The fix: ask less, in smaller pieces

Four moves shrink both sides of that budget until the job fits — and, as a bonus, make the result
*better*, not just feasible.

### Move 1 — ask only about what Python can't already resolve

Most of those 300 tokens are **values the model was never needed for**. A deterministic detector
(`detect_value_type`) already recognises amounts, dates, times, NIF/IBAN, card masks, and long id
runs — and *force-replaces* every one of them regardless of the model (see `plan_030`). So we ask
the model about **only the tokens Python couldn't resolve** — names, labels, free text:

```python
to_ask = [(i, t) for i, t in enumerate(tokens) if detect_value_type(t) is None]
```

On the receipt that's ~230 tokens instead of ~300, and the ones dropped are the ones a weak model
is *worst* at anyway (a small model miscounts figures; a regex never does).

Python force-replaces one more class the detector can't shape-match: any token matching a word in
the gitignored `.pii-denylist` (the account holder's own name/address/ids the user listed). A
personal name is *not* value-shaped — it's just a word — so without this it would rely entirely on
the model, which can miss it. The denylist makes the holder's own identity deterministic too; only
*third-party* names still depend on the model (and human review).

### Move 2 — ask for only the answer that matters

We don't need a verdict for every token — `keep` is the safe default (and value tokens are handled
in Move 1). So the model returns **only the tokens to replace**: the personal-data ones (names,
addresses, account/reference codes). Everything it doesn't mention stays kept.

```python
class _Repl(BaseModel):
    index: int
    type: Literal["text", "id"]

class _Result(BaseModel):
    replace: list[_Repl]          # usually a handful of entries, often empty
```

This collapses the output from ~one-entry-per-token to *just the PII*, which on a receipt is a few
items. Tiny output = fits the window and generates fast.

### Move 3 — give only the context that matters

The model still needs to *read* the document to judge "name vs label," but not the **whole** one
per call. For each batch we include only the statement lines that actually contain the batch's
tokens:

```python
def _relevant_lines(lines, batch_tokens, cap=80):
    return "\n".join(ln for ln in dedup(lines) if any(tok in ln for tok in batch_tokens))
```

Enough local context to decide; a fraction of the prompt size.

### Move 4 — chunk the rest, then merge by index

Split what's left into batches of **40** and merge every batch's answers back into one label list,
indexed to the original document. Each batch re-indexes its tokens `0..39` locally; we map back to
the global index on return, so a batch can't confuse *its* token 5 with the document's token 5:

```python
chunk = 40
for start in range(0, len(to_ask), chunk):
    batch = to_ask[start : start + chunk]
    context = _relevant_lines(lines, {t for _gi, t in batch})
    result = _classify_batch(context, batch, feedback)
    for repl in result.replace:                    # repl.index is 0..39, local to the batch
        gi, token = batch[repl.index]              # → global index
        labels[gi] = Classification(token, kind="replace", type=repl.type)
```

Anything the model omits stays `keep`; if an omitted token was actually a *value*, the deterministic
safety gate catches it and aborts (fail loud), so a dropped label can't become a silent leak.

## The whole shape

```text
   ~300 distinct tokens
          │
          ▼   Move 1: drop value-shaped tokens (Python force-replaces them)
   ~230 "ask the model" tokens
          │
          ▼   Move 4: batches of 40, each with only its relevant lines (Move 3)
   [40][40][40] …  ──▶  model returns ONLY the PII to replace (Move 2)
          │
          ▼   merge every batch's answers back by global index
   one complete label list  ─────────────────────────────┐
                                                          ▼
                              force-replace values + apply shape-preserving fakes to the ONE bundle
                                                          │
                                                          ▼
                                          sample.layout.json  (the whole document, reassembled)
```

By the time anything is written, the chunks are gone — one label per token, one anonymized bundle.
The chunking is invisible in the result.

## Why it doesn't hurt quality — and actually helps

Two worries, both answered:

- **"Does labelling 40 at a time make worse decisions than seeing all 230?"** No — each token's
  keep/replace decision is *independent given its context*. A token is a name or a label based on
  what it is and the lines it sits in, not on how some token 200 lines away was labelled. Every
  batch sees its relevant lines, so it has what the decision depends on.
- **"Isn't this just a workaround?"** It's the opposite — it *improved* fidelity. The original
  "label every token" prompt made a small model over-eager: it marked structural headers and product
  names as `replace`, so they got scrambled. Once Python owns the values (Move 1) and the model only
  has to *name the PII* (Move 2), the same 3B model keeps `IVA DESCRICAO VALOR`, the store name, and
  product descriptions intact while still scrubbing every amount, NIF, and card number. The
  over-scrubbing we first blamed on "the model is too small" was really the prompt asking it to do
  too much.

(Contrast a task where the pieces *do* interrelate — "pick the single largest amount." There you
couldn't chunk naïvely, because the answer spans chunks. Token labelling isn't that shape, which is
why chunk-and-merge is safe for it.)

## Two operational lessons from the real runs

- **Fail fast on a runaway model.** Each classify call caps generation (`max_tokens`), because a
  model that keeps emitting past a small replace-list isn't producing our schema — better to error
  in seconds than to grind.
- **The model must *terminate* under structured output.** Not every model plays well with Ollama's
  grammar-constrained JSON. One 12B model never emitted a stop token, ran to its ceiling on every
  call, *and* its stuck server-side generation blocked the Ollama queue for other models for
  40+ minutes. A model whose small sibling already works (same family) is the safe choice; size is
  not what makes this work — the four moves above are.

## Where this connects to the bigger picture

This all happens **inside producing the anonymized bundle**. The later step — a Claude agent writing
a parser for the new institution (`plan_029`) — receives the *one* reassembled file, never the
chunks. So there's no "reconnect the pieces" problem downstream: chunking is a concession to the
*local model's* window, fully resolved before the artifact ever leaves the machine.

## Trade-offs and knobs

- **Chunk size (40).** Bounds the output list per call; raise it to cut round-trips on a roomy
  window, lower it for a tiny one.
- **Context cap (80 lines).** Bounds the prompt; a token that appears in many lines (a common word)
  pulls in more context, so this keeps it in check.
- **A bigger window instead.** Raising `num_ctx` spends spare RAM (the KV cache) so more fits per
  call — complementary to chunking, which is the model-agnostic fix that works even on a small
  default window.

## Where it lives

- `llm.ollama_token_classifier` — the chunked Ollama classifier (the four moves).
- `parsergen.anonymize.Classifier` — the protocol it satisfies; the orchestrator that force-replaces
  values and applies fakes.
- `parsergen.gates.detect_value_type` — the deterministic detector that decides what the model never
  needs to see.
