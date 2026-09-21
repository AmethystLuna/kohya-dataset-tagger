---
name: tagger-gpu-provider
description: 2026-09-19, the WD14 tagger's default provider list omitted DirectML, so on the DirectML install the setup scripts produce it silently ran on CPU (0.75 s/image instead of 0.20); the default is now CUDA -> DirectML -> CPU
metadata:
  type: topic
---

# The tagger was running on CPU by accident (2026-09-19)

Found while measuring the batch paths: `tools/check_provider.py` reported DirectML as active, but
the tagger itself never asked for it - so the "DirectML 0.192 s/image" row in current-state was a
capability, not the shipped behaviour.

## Root cause

`autotag/wd14.py` had

```python
DEFAULT_PROVIDERS = ("CUDAExecutionProvider", "CPUExecutionProvider")
```

and `api/autotag.py::_session` builds the tagger with **no explicit `providers=`**.
`WD14Tagger.__init__` filters the request against `ort.get_available_providers()`, so on a
DirectML build (`['DmlExecutionProvider', 'CPUExecutionProvider']`) the CUDA entry was dropped and
the session was created with CPU only. Onnxruntime falls back to CPU **silently**, so nothing failed and
nothing warned; only `InferenceSession.get_providers()` shows it.

`check_provider.py` looked healthy because that tool picks the first available GPU provider itself
and passes it explicitly - the tagger does not. The same is true of the spec's benchmark table
(`tools/bench_providers.py --provider DmlExecutionProvider`).

Measured here (onnxruntime-directml 1.23.0, wd-swinv2-tagger-v3, 2 warm-up images discarded + 6 real
images; DirectML compiles kernels on first use, so warm-up is mandatory):

| Configuration | `session.get_providers()` | Median | 1653 images |
|---|---|---|---|
| Before (shipped default) | `['CPUExecutionProvider']` | 0.746 s/image | 20.6 min |
| After (shipped default) | `['DmlExecutionProvider', 'CPUExecutionProvider']` | 0.196 s/image | 5.4 min |

## The fix

`DEFAULT_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider")`,
and the filtering rule moved into a pure `select_providers(requested, available)` so it is testable
without a model or a GPU. CUDA still wins when the build has it (the `-Gpu cuda` route keeps its
~14% edge); a CPU-only build is unchanged.

## Criteria and falsification

- `test/test_wd14_integration.py::test_provider_order_prefers_a_gpu_and_falls_back_to_cpu` (new,
  pure): a CUDA build -> CUDA first; a DirectML build -> DirectML + CPU; CPU-only -> CPU; an explicit
  request keeps its own order and only loses unavailable entries; nothing available raises.
  **Falsified**: putting the old two-entry tuple back turns it red
  (`assert ['CPUExecutionProvider'] == ['DmlExecutionProvider', 'CPUExecutionProvider']`).
- `test_input_contract_matches_the_spec_baseline` no longer pins
  `('CPUExecutionProvider',)` - p0-spec §7 already said that assertion turns red the moment the
  venv's package changes. It now asserts the reported tuple equals
  `session.get_providers()` and that the first entry is the best available provider, which catches
  the *other* silent-fallback case: a provider that is compiled in but cannot load.

---

Related: [wd14-tagger](../wd14-tagger.md), [batch-performance](batch-performance.md), [p0-spec](../p0-spec.md)
