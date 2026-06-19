# The math behind TEA

TEA assigns each request a single composite score. Every other metric in the
system feeds into it. The score is built to be auditable: each component has a
closed form, all weights are explicit, and the worst case is bounded.

## Notation

- `P` is the full prompt: system instructions, the user query, retrieved
  context, and any few-shot examples. Its length in tokens is `|P|`.
- `C` is the retrieved-context subsection of `P`.
- `Y` is the completion the model produces, of length `|Y|`.
- `Y*` is a reference or ground-truth completion when one is available.
- `Q(Y, Y*)` is the quality score for this request, in `[0, 1]`. The caller
  chooses the function: exact match for closed-domain QA, a judge model for
  open-ended generation, a task metric for agent workflows.

## Token efficiency

Token efficiency is the quality-weighted information yield per input token:

```
TokenEff(P, Y) = ( Q(Y, Y*) * |Y| ) / ( |P| + |Y| )
```

It sits in `[0, 1]`. A tight prompt that yields a long, high-quality answer
pushes it up; a bloated prompt that yields a short useless answer pushes it
down. Compression ratio alone is the wrong objective: a ten-token prompt that
hallucinates a long reply has high compression and zero value. Multiplying by
`Q` ties the metric to useful output.

## Effective context: precision and recall

The model uses only some of the context shipped to it. With a threshold `tau`
on the attention weight, the set of attended tokens is:

```
C_used(tau) = { t in C : max_i A(t | P, Y_<i) >= tau }
```

Given a ground-truth relevant set `C*`:

```
Precision(C) = |C_used ∩ C*| / |C_used|
Recall(C)    = |C_used ∩ C*| / |C*|
F1(C)        = 2 * Precision * Recall / (Precision + Recall)
```

When `C` is large but `C_used` is small, the retriever is over-retrieving and
should pay less per call. When `C_used` saturates `C` yet quality is poor, the
retriever is under-retrieving and the context needs to expand.

## Context utilisation

```
Util(C) = |C_used(tau)| / |C|
```

The cheapest knob: when utilisation is low, compress; when it is high and
quality is still low, expand.

## Cost

```
Cost_GPU(P, Y) = c_pre * |P| + c_dec * |Y|
```

For autoregressive decoding `c_dec` is typically 3 to 10 times `c_pre`. The
normaliser `Cost_max` is the caller's per-request cost ceiling; a sensible
default is the 95th percentile of historical per-request cost on the workload,
which keeps the penalty in `[0, 1]` for normal traffic.

## The composite score

```
S(P) = a * TokenEff(P, Y)
     + b * Q(Y, Y*)
     - c * ( Cost_GPU(P, Y) / Cost_max )
     - d * ( 1 - Util(C) )
```

with `a, b, c, d >= 0` and `a + b + c + d = 1`. The shipped default is
`(0.30, 0.40, 0.20, 0.10)`. Weights are meant to be learned per workload, not
hand-tuned, by fitting against the caller's stated objective.

A note on scale: `TokenEff` in practice sits around `0.01` to `0.10` on
long-context workloads, while the other terms sit in `[0, 1]`. The `a` term is
therefore a directional signal. Teams that want it to dominate can replace the
raw value with its rank-percentile against the workload's distribution.

## The optimisation problem

```
P* = argmax over P' in T(P) of S(P')
subject to:
    Q(Y(P'), Y*) >= Q_min
    |P'| <= |P|
    Mem_KV(P', Y) <= M_max
```

where `T(P)` is the set of valid transforms: compression, reordering,
deduplication, dropping low-utility context, summarising retrieved segments,
and switching few-shot to zero-shot when confidence allows.

## When the model is closed

For providers that do not expose attention, estimate `C_used` by ablation.
Chunk the context, then for each chunk `c`:

```
A_hat(c) = KL( p(Y | P) || p(Y | P \ c) )
```

Chunks whose removal materially changes the output distribution are the ones
the model used. Calibration is offline and amortised across many calls.

## What this open release implements

The library implements the deterministic side: token counting, the composite
score in `tea.score()`, and a lexical-overlap proxy for `Util` and for
`drop_context`. It does not read attention weights or run KL ablation; those
are the production path described above. The proxy errs toward keeping a chunk
rather than dropping a useful one, and `drop_context` removes at most 70 per
cent of the context in one pass so a proxy mistake cannot gut the prompt.
