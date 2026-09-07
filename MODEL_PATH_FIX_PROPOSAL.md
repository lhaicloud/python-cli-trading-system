# Model path correctness — forward-only, keep ML off

**Status:** implemented in code. This is a path-correctness fix, **not** an ML enablement.
**Do not** bulk-symlink, relocate, or re-register active model files.

## Problem

Active rows in `model_versions` often point at a path that does not exist on
disk. A common nest is `{MODEL_DIR}/{MODEL_DIR.name}/file.joblib`
(`data/models/models/...`) from joining `model_dir` twice on save. Load uses
the registered path as-is (`Path(file_path).exists()`). Missing file ⇒
`get_current_model_path` returns `None` ⇒ BUY/SELL score **0.0**.

That inert state is load-bearing: turning the files back on (symlink, move,
or fallback) would suddenly apply ML to signals.

## Forward-only fix

`app/models/versioning.py`:

- `canonical_model_dir` collapses only a trailing `models/models` nest.
- `join_model_dir` always saves `model_dir / basename(path)` — never
  `data/models/models/<file>`.
- `model_file_path` and `register_model` use that join so **new** dumps and
  DB paths are single-nested.
- `get_current_model_path` still returns `None` when the **registered** path
  is missing.

Existing broken actives stay inert. Retrain/validate/activate is required
before any model can score, same as before.

## `MODEL_PATH_FALLBACK_NESTED` (default **False**)

Opt-in lookup of `{MODEL_DIR}/models/<basename>` (or `{MODEL_DIR}/{MODEL_DIR.name}/<basename>`)
when the registered path is missing.

**Footgun:** setting this true can load joblib files that were previously
invisible and start producing non-zero ML scores. Leave it false unless a
human has explicitly decided to re-enable those files. Do not flip it on as
a "quick fix".

## What this change does **not** do

- No bulk copy/symlink/relocate of `data/models/`.
- No UPDATE of `model_versions.file_path` for existing actives.
- Does not set `DISABLE_ML_MODEL`. Signal generation still uses ML **if** a
  registered path exists; broken actives continue to score 0.0.
- Does not change training algorithms or features.

## Tests

```bash
python -m scripts.test_model_path
```
