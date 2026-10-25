"""
Authoritative (node_class, input_key) → folder sources for /object_info combo rebuild.

Maps loader dropdowns to isolation list_display_names folders with optional display
prefixes (e.g. bbox/, segm/). Unmapped (class, input) pairs must not substitute a
folder list — callers strip UUID prefixes only.
"""

from __future__ import annotations

import logging
from .isolation_resolver import foreign_uuid_prefix, is_uuid_segment, list_display_names
from .model_isolation import sanitize_user_segment, user_isolation_candidate_segments
from .model_visibility_policy import normalize_model_name
from .prompt_model_validator import NODE_MODEL_INPUTS, _looks_like_model_file

logger = logging.getLogger(__name__)

# (folder, display_prefix) — prefix is prepended to each display name from that folder.
ComboSource = list[tuple[str, str]]

# Explicit multi-folder / pack-specific overrides (authoritative over NODE_MODEL_INPUTS seed).
_COMBO_SOURCE_OVERRIDES: dict[tuple[str, str], ComboSource] = {
	("UltralyticsDetectorProvider", "model_name"): [
		("ultralytics_bbox", "bbox/"),
		("ultralytics_segm", "segm/"),
	],
	("easy ultralyticsDetectorPipe", "model_name"): [
		("ultralytics_bbox", "bbox/"),
		("ultralytics_segm", "segm/"),
	],
}

# Extra (class, input) → sources for nodes not fully represented as 1:1 in NODE_MODEL_INPUTS,
# or to force empty-prefix single folders explicitly.
_COMBO_SOURCE_EXTRAS: dict[tuple[str, str], ComboSource] = {
	# Impact / Easy-Use SAM (input is model_name, not sam_name)
	("SAMLoader", "model_name"): [("sams", "")],
	("easy samLoaderPipe", "model_name"): [("sams", "")],
	("easy hiresFix", "model_name"): [("upscale_models", "")],
	("DiffusionModelLoaderKJ", "model_name"): [("diffusion_models", "")],
	("DiffusionModelSelector", "model_name"): [("diffusion_models", "")],
	("easy diffusionModelLoader", "model_name"): [("diffusion_models", "")],
	("easy diffusionModelLoader", "clip_name"): [("text_encoders", "")],
	("easy svdLoader", "clip_name"): [("text_encoders", "")],
	("easy cascadeLoader", "clip_name"): [("text_encoders", "")],
	("easy pixArtLoader", "clip_name"): [("text_encoders", "")],
	# CLIP vision MultiGPU
	("CLIPVisionLoaderMultiGPU", "clip_name"): [("clip_vision", "")],
	("CLIPVisionLoaderDisTorch2MultiGPU", "clip_name"): [("clip_vision", "")],
	# CLIP text MultiGPU / GGUF (GGUF lists share text_encoders + legacy clip dual-read)
	("CLIPLoaderMultiGPU", "clip_name"): [("text_encoders", "")],
	("CLIPLoaderDisTorch2MultiGPU", "clip_name"): [("text_encoders", "")],
	("CLIPLoaderGGUF", "clip_name"): [("text_encoders", "")],
	("CLIPLoaderGGUFMultiGPU", "clip_name"): [("text_encoders", "")],
	("CLIPLoaderGGUFDisTorchMultiGPU", "clip_name"): [("text_encoders", "")],
	("CLIPLoaderGGUFDisTorch2MultiGPU", "clip_name"): [("text_encoders", "")],
	("DualCLIPLoaderMultiGPU", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderMultiGPU", "clip_name2"): [("text_encoders", "")],
	("DualCLIPLoaderDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("DualCLIPLoaderGGUF", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderGGUF", "clip_name2"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFMultiGPU", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFMultiGPU", "clip_name2"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFDisTorchMultiGPU", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFDisTorchMultiGPU", "clip_name2"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("DualCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderMultiGPU", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderMultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderMultiGPU", "clip_name3"): [("text_encoders", "")],
	("TripleCLIPLoaderDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderDisTorch2MultiGPU", "clip_name3"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUF", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUF", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUF", "clip_name3"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFMultiGPU", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFMultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFMultiGPU", "clip_name3"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name3"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("TripleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderMultiGPU", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderMultiGPU", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderMultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderMultiGPU", "clip_name4"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderDisTorch2MultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderDisTorch2MultiGPU", "clip_name4"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUF", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUF", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUF", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUF", "clip_name4"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFMultiGPU", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFMultiGPU", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFMultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFMultiGPU", "clip_name4"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorchMultiGPU", "clip_name4"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name1"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name2"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name3"): [("text_encoders", "")],
	("QuadrupleCLIPLoaderGGUFDisTorch2MultiGPU", "clip_name4"): [("text_encoders", "")],
}


def _seed_from_node_model_inputs() -> dict[tuple[str, str], ComboSource]:
	"""Build 1:1 (class, input) → [(folder, "")] from NODE_MODEL_INPUTS."""
	out: dict[tuple[str, str], ComboSource] = {}
	for class_type, mappings in NODE_MODEL_INPUTS.items():
		for folder, input_key in mappings:
			key = (class_type, input_key)
			entry = (folder, "")
			existing = out.get(key)
			if existing is None:
				out[key] = [entry]
			elif entry not in existing:
				existing.append(entry)
	return out


def _build_table() -> dict[tuple[str, str], ComboSource]:
	table = _seed_from_node_model_inputs()
	table.update(_COMBO_SOURCE_EXTRAS)
	table.update(_COMBO_SOURCE_OVERRIDES)
	return table


OBJECT_INFO_COMBO_SOURCES: dict[tuple[str, str], ComboSource] = _build_table()

# One warning per (class, input) per process for overlap fallback.
_OVERLAP_WARNED: set[tuple[str, str]] = set()


def sources_for(class_type: str | None, input_key: str | None) -> ComboSource | None:
	"""Return ComboSource for (class_type, input_key), or None if unmapped."""
	if not class_type or not input_key:
		return None
	return OBJECT_INFO_COMBO_SOURCES.get((class_type, input_key))


def strip_uid_prefixes(combo: list[str], viewer_user_id: str | None) -> list[str]:
	"""
	Uid-strip-only for unmapped combos.

	- Strip the viewer's own isolation prefix (username or UUID).
	- Drop entries whose first segment is another user's isolation folder.
	- Leave HF repo IDs, bbox/…, sentinels like None, and plain names untouched.
	"""
	candidate_segs = (
		[s.lower() for s in user_isolation_candidate_segments(viewer_user_id)]
		if viewer_user_id
		else []
	)
	out: list[str] = []
	for item in combo:
		if not isinstance(item, str):
			continue
		name = item.replace("\\", "/").strip()
		if not name:
			out.append(item)
			continue
		matched_own = False
		for seg in candidate_segs:
			prefix = f"{seg}/"
			if name.lower().startswith(prefix):
				out.append(name[len(seg) + 1 :])
				matched_own = True
				break
		if matched_own:
			continue
		parts = name.split("/", 1)
		if len(parts) >= 2:
			if is_uuid_segment(parts[0]) or foreign_uuid_prefix(name, viewer_user_id):
				# Foreign isolation directory — never expose
				continue
		out.append(name)
	return out


def _is_sentinel(value: str) -> bool:
	"""True for non-file combo options (e.g. None) that should be preserved."""
	if not value or not isinstance(value, str):
		return False
	v = value.strip()
	if not v:
		return True
	if _looks_like_model_file(v):
		return False
	# Slash paths that look like models/HF repos are not sentinels
	norm = v.replace("\\", "/")
	if "/" in norm and (_looks_like_model_file(norm) or is_uuid_segment(norm.split("/", 1)[0])):
		return False
	# Common Comfy sentinels
	if v in ("None", "none", "Baked VAE", "Baked CLIP"):
		return True
	# No extension and no path → treat as sentinel option
	if "/" not in norm and "." not in v:
		return True
	return False


def preserve_sentinels(original: list[str], rebuilt: list[str]) -> list[str]:
	"""Insert non-file sentinel options from original at their original indices."""
	if not original:
		return list(rebuilt)
	sentinels: list[tuple[int, str]] = [
		(i, v) for i, v in enumerate(original) if isinstance(v, str) and _is_sentinel(v)
	]
	if not sentinels:
		return list(rebuilt)
	out = list(rebuilt)
	for idx, val in sentinels:
		if val in out:
			continue
		insert_at = min(idx, len(out))
		out.insert(insert_at, val)
	return out


def build_combo_from_sources(
	sources: ComboSource,
	user_id: str | None,
	*,
	grants: list[dict] | None = None,
	view_all: bool = False,
	original: list[str] | None = None,
) -> list[str]:
	"""Rebuild a combo from one or more folders with optional display prefixes."""
	seen: set[str] = set()
	out: list[str] = []
	for folder, prefix in sources:
		names = list_display_names(folder, user_id, grants=grants, view_all=view_all)
		for name in names:
			display = f"{prefix}{name}" if prefix else name
			key = normalize_model_name(display)
			if key in seen:
				continue
			seen.add(key)
			out.append(display)
	if original:
		out = preserve_sentinels(original, out)
	return out


def _norm_for_overlap(name: str, viewer_user_id: str | None) -> str:
	n = name.replace("\\", "/").strip().lower()
	for seg in user_isolation_candidate_segments(viewer_user_id) if viewer_user_id else []:
		prefix = f"{seg.lower()}/"
		if n.startswith(prefix):
			return n[len(prefix) :]
	return n


# Folders commonly involved in cross-input-name bleed (model_name / clip_name collisions).
_COMPETING_FOLDERS: tuple[str, ...] = (
	"upscale_models",
	"text_encoders",
	"clip",
	"clip_vision",
	"sams",
	"checkpoints",
	"loras",
	"diffusion_models",
	"unet",
	"ultralytics_bbox",
	"ultralytics_segm",
	"ultralytics",
	"controlnet",
	"vae",
)


def _original_looks_like_cross_folder_bleed(
	orig_modelish: list[str],
	sources: ComboSource,
	viewer_user_id: str | None,
	*,
	grants: list[dict] | None,
	view_all: bool,
) -> bool:
	"""True when original entries appear in a different asset folder than this mapping."""
	mapped = {f for f, _p in sources}
	orig_norms = {_norm_for_overlap(x, viewer_user_id) for x in orig_modelish}
	orig_bases = {n.rsplit("/", 1)[-1] for n in orig_norms}
	for folder in _COMPETING_FOLDERS:
		if folder in mapped:
			continue
		try:
			names = list_display_names(folder, viewer_user_id, grants=grants, view_all=view_all)
		except Exception:
			continue
		folder_norms = {_norm_for_overlap(n, viewer_user_id) for n in names}
		folder_bases = {n.rsplit("/", 1)[-1] for n in folder_norms}
		if orig_norms & folder_norms or orig_bases & folder_bases:
			return True
	return False


def overlap_or_fallback(
	class_type: str,
	input_key: str,
	original: list[str],
	rebuilt: list[str],
	viewer_user_id: str | None,
	*,
	view_all: bool,
	sources: ComboSource | None = None,
	grants: list[dict] | None = None,
) -> list[str]:
	"""
	If rebuilt shares zero entries with original model-like names (and rebuilt is
	non-empty or view_all), fall back to uid-strip-only original when the miss
	looks like a table/pack mismatch. Cross-folder bleed originals (e.g. upscale
	names on a CLIPVision combo) keep the rebuilt list. Empty rebuilt for
	non-view_all with no grants is kept (no leak).
	"""
	if not view_all and not rebuilt:
		return rebuilt

	orig_modelish = [
		x
		for x in original
		if isinstance(x, str)
		and (_looks_like_model_file(x) or ("/" in x.replace("\\", "/") and not _is_sentinel(x)))
	]
	if not orig_modelish:
		return rebuilt

	orig_norms = {_norm_for_overlap(x, viewer_user_id) for x in orig_modelish}
	reb_norms = {_norm_for_overlap(x, viewer_user_id) for x in rebuilt if isinstance(x, str)}
	if orig_norms & reb_norms:
		return rebuilt

	# Basename match across prefix shapes (face.pt vs bbox/face.pt)
	reb_bases = {n.rsplit("/", 1)[-1] for n in reb_norms}
	orig_bases = {n.rsplit("/", 1)[-1] for n in orig_norms}
	if reb_bases & orig_bases:
		return rebuilt

	src = sources or []
	if src and _original_looks_like_cross_folder_bleed(
		orig_modelish, src, viewer_user_id, grants=grants, view_all=view_all
	):
		return rebuilt

	key = (class_type, input_key)
	if key not in _OVERLAP_WARNED:
		_OVERLAP_WARNED.add(key)
		logger.warning(
			"object_info combo overlap miss for %s.%s — falling back to original (uid-strip only)",
			class_type,
			input_key,
		)
	return strip_uid_prefixes(original, viewer_user_id)
