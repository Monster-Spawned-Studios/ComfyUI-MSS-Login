# --- START OF FILE utils/prompt_model_validator.py ---
"""
Extract model references from ComfyUI API-format prompt and validate against a per-user allow-set.
Used to block POST /prompt when the workflow references models the user is not permitted to use.
"""

from typing import Any, Optional

from .model_visibility_policy import grant_matches_item, normalize_model_name

# Map: node class_type -> list of (folder, input_key) for that node.
# Folder names must match folder_paths / model_filter_middleware (checkpoints, loras, vae, etc.).
# ComfyUI API format: prompt is a dict of node_id -> { "class_type": "...", "inputs": { "key": value } }.
NODE_MODEL_INPUTS: dict[str, list[tuple[str, str]]] = {
	# Checkpoints
	"CheckpointLoaderSimple": [("checkpoints", "ckpt_name")],
	"CheckpointLoader": [("checkpoints", "ckpt_name")],
	"unCLIPCheckpointLoader": [("checkpoints", "ckpt_name")],
	"ImageOnlyCheckpointLoader": [("checkpoints", "ckpt_name")],
	"unet_load": [("diffusion_models", "unet_name")],
	"UNETLoader": [("diffusion_models", "unet_name")],
	# LoRA
	"LoraLoader": [("loras", "lora_name")],
	"LoraLoaderModelOnly": [("loras", "lora_name")],
	"LoraLoaderModel": [("loras", "lora_name")],  # legacy alias
	# VAE
	"VAELoader": [("vae", "vae_name")],
	"VAEDecode": [],  # no model file
	"VAEEncode": [],
	# CLIP / text encoders (use text_encoders; clip aliases dual-read in resolver)
	"CLIPLoader": [("text_encoders", "clip_name")],
	"DualCLIPLoader": [("text_encoders", "clip_name1"), ("text_encoders", "clip_name2")],
	"TripleCLIPLoader": [
		("text_encoders", "clip_name1"),
		("text_encoders", "clip_name2"),
		("text_encoders", "clip_name3"),
	],
	"QuadrupleCLIPLoader": [
		("text_encoders", "clip_name1"),
		("text_encoders", "clip_name2"),
		("text_encoders", "clip_name3"),
		("text_encoders", "clip_name4"),
	],
	"CLIPVisionLoader": [("clip_vision", "clip_name")],
	# ControlNet, upscale, etc.
	"ControlNetLoader": [("controlnet", "control_net_name")],
	"DiffControlNetLoader": [("controlnet", "control_net_name")],
	"LoadControlNet": [("controlnet", "control_net_name")],
	"UpscaleModelLoader": [("upscale_models", "model_name")],
	"StyleModelLoader": [("style_models", "style_model_name")],
	"GLIGENLoader": [("gligen", "gligen_name")],
	"HypernetworkLoader": [("hypernetworks", "hypernetwork_name")],
	"Embedding": [("embeddings", "embedding_name")],
	# Diffusion / UNet (some custom nodes)
	"DiffusionModelLoader": [("diffusion_models", "model_name")],
	"PhotomakerLoader": [("photomaker", "photomaker_name")],
	"AudioEncoderLoader": [("audio_encoders", "audio_encoder_name")],
	# Ultralytics (Impact Suite / detection)
	"UltralyticsDetectorProvider": [
		("ultralytics_bbox", "bbox_model"),
		("ultralytics_segm", "seg_model"),
		("ultralytics", "model"),
	],
	"YOLOWorldDetectorProvider": [("ultralytics", "model")],
	# MMDet (Impact Suite / detection)
	"MMDetDetectorProvider": [
		("mmdets_bbox", "bbox_model"),
		("mmdets_segm", "seg_model"),
		("mmdets", "model"),
	],
	# SAM (Segment Anything)
	"SAMLoader": [("sams", "sam_name")],
	"LoadSAM": [("sams", "sam_name")],
	"SAMModelLoader": [("sams", "sam_name")],
	# Classifiers
	"ClassifierLoader": [("classifiers", "classifier_name")],
	"LoadClassifier": [("classifiers", "classifier_name")],
}

# Legacy folder name mapping (e.g. unet -> diffusion_models, clip -> text_encoders)
_LEGACY_FOLDER: dict[str, str] = {"unet": "diffusion_models", "clip": "text_encoders"}

# Extensions that indicate a model file (for catch-all detection of unknown node types)
_MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".engine")


def _normalize_folder(folder: str) -> str:
	return _LEGACY_FOLDER.get(folder, folder)


def _looks_like_model_file(value: str) -> bool:
	"""True if value looks like a model filename (has a known model extension)."""
	if not value or not isinstance(value, str):
		return False
	val = value.strip().lower()
	return any(val.endswith(ext) for ext in _MODEL_FILE_EXTENSIONS)


def folder_for_node_input(class_type: str | None, input_key: str) -> str | None:
	"""Return the model folder for a (class_type, input_key), or None if unknown."""
	if not class_type or not input_key:
		return None
	for folder, key in NODE_MODEL_INPUTS.get(class_type) or []:
		if key == input_key:
			return _normalize_folder(folder)
	return None


def extract_model_references(
	prompt: dict[str, Any], known_models: set[tuple[str, str]] | None = None
) -> set[tuple[str, str]]:
	"""
	Extract (folder, item_name) from a ComfyUI API-format prompt.
	Prompt format: { node_id: { "class_type": "...", "inputs": { "key": "value", ... } }, ... }.
	Returns set of (folder, item_name); folder is normalized (e.g. clip -> text_encoders).
	When known_models is provided (set of (folder, item_name)), also scans all node inputs
	for string values that look like model filenames and resolves them via known_models
	(catch-all for custom/unknown node types) using plain/storage dual-read.
	"""
	out: set[tuple[str, str]] = set()
	if not isinstance(prompt, dict):
		return out
	for _node_id, node in prompt.items():
		if not isinstance(node, dict):
			continue
		class_type = node.get("class_type")
		if not class_type or not isinstance(class_type, str):
			continue
		mappings = NODE_MODEL_INPUTS.get(class_type)
		seen_keys: set[str] = set()
		if mappings:
			inputs = node.get("inputs")
			if isinstance(inputs, dict):
				for folder, input_key in mappings:
					value = inputs.get(input_key)
					if isinstance(value, str) and value.strip():
						item_name = value.strip()
						folder_norm = _normalize_folder(folder)
						out.add((folder_norm, item_name))
						seen_keys.add(input_key)
		# Catch-all: any input value that looks like a model file
		if known_models:
			inputs = node.get("inputs")
			if isinstance(inputs, dict):
				for key, value in inputs.items():
					if key in seen_keys:
						continue
					if isinstance(value, str) and value.strip() and _looks_like_model_file(value):
						item_name = value.strip()
						norm_item = normalize_model_name(item_name)
						for folder, name in known_models:
							if grant_matches_item(norm_item, {normalize_model_name(name)}):
								out.add((_normalize_folder(folder), item_name))
	return out


def validate_prompt_models(
	allowed_set: set[tuple[str, str]],
	allow_all: bool,
	prompt: dict[str, Any],
	known_models: set[tuple[str, str]] | None = None,
) -> tuple[bool, str | None]:
	"""
	Validate that every model reference in the prompt is in the allowed set.
	allowed_set: set of (folder, item_name) the user may use (storage or flat).
	allow_all: if True, skip validation (admin or can_view_all_comfyui_items).
	known_models: optional set of (folder, item_name) for catch-all filename resolution.
	Returns (is_valid, error_message). error_message is set when is_valid is False.

	Dual-reads plain display names against storage-shaped grant rows (and aliases).
	"""
	if allow_all:
		return (True, None)

	# Index allowed names by folder (plus legacy aliases)
	by_folder: dict[str, set[str]] = {}
	for folder, item_name in allowed_set:
		norm = normalize_model_name(item_name)
		by_folder.setdefault(folder, set()).add(norm)
		# Alias folders so unet ↔ diffusion_models grants both match
		if folder == "diffusion_models":
			by_folder.setdefault("unet", set()).add(norm)
		elif folder == "unet":
			by_folder.setdefault("diffusion_models", set()).add(norm)
		elif folder == "text_encoders":
			by_folder.setdefault("clip", set()).add(norm)
		elif folder == "clip":
			by_folder.setdefault("text_encoders", set()).add(norm)

	refs = extract_model_references(prompt, known_models=known_models)
	for folder, item_name in refs:
		allowed_norms = by_folder.get(folder) or set()
		# Also check normalized folder
		folder_n = _normalize_folder(folder)
		if folder_n != folder:
			allowed_norms = allowed_norms | (by_folder.get(folder_n) or set())
		if (folder, item_name) in allowed_set or (folder_n, item_name) in allowed_set:
			continue
		if grant_matches_item(normalize_model_name(item_name), allowed_norms):
			continue
		return (False, f"Model not allowed: {folder}/{item_name}")
	return (True, None)
