---
name: pip-index-profiles
description: 2026-09-19 the environment-setup scripts split into a "regular" profile and a "China" profile (setup_env_cn.*): why the ordered fallback, why TUNA is not in the profile, and the ps1 gaining -DryRun
metadata:
  type: topic
---

# Environment-script index profiles: regular / China

## What it is

Environment setup changed from "one script, pinned to pypi.org" into **two user entry points + an optional index profile**:

| Entry point | Platform | Index profile |
|---|---|---|
| `setup_env.bat` / `setup_env.sh` | Windows / Linux | `pypi` (the regular profile, official https://pypi.org/simple only) |
| `setup_env_cn.bat` / `setup_env_cn.sh` | Windows / Linux | `cn` (the China profile, ordered fallback USTC -> Aliyun -> pypi.org) |

Both entry points are **thin wrappers**; the real implementation lives in `scripts/setup_env.ps1` / `scripts/setup_env.sh`,
and they only inject one extra switch (`-Index cn` / `--index cn`). `KOHYA_TAGGER_PIP_INDEX=a,b` replaces the whole profile.
The `.ps1` also gained `-DryRun` (before this only the `.sh` had `--dry-run`), so the index profile on both platforms can be asserted by machine.

## Why "ordered fallback" instead of "pin one source"

"Which mirror is fast" **varies by machine and by time**, and this repository has two contradictory measurements of its own (same method: Range-fetch 8 MB / 12 s):

| Source | 2026-09-17 | 2026-09-19 |
|---|---|---|
| Aliyun | 0.07 MB/s (58 minutes; it just looks hung) | 0.86–1.25 MB/s |
| pypi.org / files.pythonhosted | 1.45–5.8 MB/s | 0.55–1.01 MB/s |
| USTC | not measured | **8.4–11 MB/s** |
| TUNA | not measured | simple index page 200, direct wheel file 403 |

So the contract is not "use a particular source" but two more stable things:

1. **Every pip call passes `--index-url` explicitly**, never falling back to the global pip config (the 0.07 MB/s was caused by the global source).
   This unified the `.ps1` too: before this it pinned the source only for the onnxruntime step, while the pip upgrade / base dependencies /
   editable install went through the global config.
2. **The China profile falls back in order**, ending with the official `pypi.org` — so the China profile **can never be worse than the regular profile**,
   and a temporarily dead mirror still installs.

## Why TUNA is not in the profile (and why it does not say "China mirrors are always faster")

Measured on 2026-09-19: TUNA's `simple/onnxruntime-gpu/` index page returns 200, but the wheel links it hands out
return `403` (apparently hotlink protection or a UA restriction). **An index page that opens ≠ a file that downloads**, so we left it
out of the fallback chain. A different network/machine may give a different conclusion — to use another mirror, set `KOHYA_TAGGER_PIP_INDEX`.

## Criteria and falsification

- `test/test_shell_scripts.py`: in the cn profile's dry-run the first choice must be a China mirror and the last must be
  `pypi.org`; `setup_env_cn.sh` and `scripts/setup_env.sh --index cn` produce the same result;
  `.ps1 -Index cn -DryRun` aligns with the `.sh`; the two cn wrappers are thin wrappers (containing the injected switch).
- **Falsification**: `test_pip_gate_goes_red_on_the_wrong_index_for_the_profile` —
  a regular profile with a China mirror mixed in, or a cn profile with the official source first, must both go red.
- `acceptance/test_p0_acceptance.py` A32 got matching assertions for the cn profile and `setup_env_cn.sh`.

## Not verified

- **`setup_env_cn.sh` was never really run** (it would touch the shared `.venv` and download 245 MB). What was verified is the
  dry-run plan and the index-profile choice; the fallback logic (only move to the next source when the previous one fails) exists only as a
  code path and was not exercised by inducing a failure.
- TUNA's 403 was measured only once on this machine, not rechecked on another network / UA; and the small dependency
  packages under `simple` were not measured mirror by mirror (only the one 245 MB big wheel, onnxruntime-gpu).
- The cn profile has not been run on a real Linux machine (this machine has no WSL distribution).

---

Related: [p0-spec](../p0-spec.md) (§7 environment and the three hard lessons), [documentation-style](../documentation-style.md)
