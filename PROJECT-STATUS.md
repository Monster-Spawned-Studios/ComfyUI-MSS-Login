# Project status

## 2026-10-16

- **Bug:** With experimental model isolation on, authenticated `/object_info` loader dropdowns showed models from the wrong folders (e.g. `model_name` → upscale models, `clip_name` → text encoders) even though `/api/models/<folder>` was correct.
- **Root cause:** Combo rebuild used a single-folder lookup that did not carry multi-folder display prefixes and left many pack nodes unmapped (stale `NODE_MODEL_INPUTS` keys such as `bbox_model`/`sam_name`); unmapped combos were grant-filtered instead of passed through.
- **Fix:** Authoritative `(node_class, input)` `OBJECT_INFO_COMBO_SOURCES` table in `utils/object_info_combo_sources.py` (multi-folder + prefixes for Ultralytics); `filter_object_info_payload` rebuilds mapped combos via `list_display_names`; unmapped combos use uid-strip-only passthrough (Florence HF IDs preserved); overlap fallback for table/pack mismatches without leaking grants; `NODE_MODEL_INPUTS` updated for Subpack/MultiGPU/GGUF/Easy-Use/KJNodes.
- **Tests:** `tests/run_object_info_combo_mapping_tests.py` (+ pytest wrapper) wired into `tests/run_ci.py`.
- **Preserved:** 4f1f1ef plain-name isolation (viewer UUID stripped, foreign UUID prefixes never exposed, grants honored on mapped rebuilds).

*Note: This file was created at the repo root; it was not present on `development` at the prior sync tip.*
