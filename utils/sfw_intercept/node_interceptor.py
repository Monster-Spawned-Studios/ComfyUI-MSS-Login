# --- START OF FILE utils/node_interceptor.py ---
import torch
import nodes
import numpy as np
from PIL import Image
import latent_preview

from ...utils.sfw_intercept.nsfw_guard import (
	_get_nsfw_pipeline,
	is_sfw_enforced_for_current_session,
)

# --- CONFIGURATION ---
SCORE_THRESHOLD = 0.50


# ----------------------------------------------------------------------------
# PART 1: The Scanner
# ----------------------------------------------------------------------------
def check_tensor_nsfw(images_tensor):
	# 1. CHECK USER PERMISSIONS FIRST
	# Use quiet mode to reduce logging during node execution
	if not is_sfw_enforced_for_current_session(quiet=True):
		# print("[MSS-Login] 🛡️ SFW Disabled for this user. Bypassing scan.")
		return False

	# 2. Run Scan
	print("[MSS-Login] 🔍 Interceptor: Analysis starting...")
	pipeline = _get_nsfw_pipeline()
	if pipeline is None:
		print("[MSS-Login] ⚠️ WARN: Model failed. BLOCKING (Fail-Safe).")
		return True

	try:
		if images_tensor is None or len(images_tensor) == 0:
			return False

		i = 255.0 * images_tensor[0].cpu().numpy()
		img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

		results = pipeline(img)

		if not results:
			return False

		# Scan all labels for an explicit "nsfw" entry (matching the file-path
		# classifier logic in nsfw_guard._classify_image_path).
		for entry in results:
			label = (entry.get("label") or "").lower()
			score = float(entry.get("score", 0.0))
			if label == "nsfw" and score > SCORE_THRESHOLD:
				print(f"[mss-login] 🛑 BLOCKED NSFW (Score {score:.4f})")
				return True

	except Exception as e:
		print(f"[mss-login] Interceptor Error: {e}")
		return True

	return False


# ----------------------------------------------------------------------------
# PART 2: The Kill Switch
# ----------------------------------------------------------------------------
def disable_latent_previews():
	# Only disable previews if the CURRENT user needs protection
	# But since previewers are global singletons in ComfyUI,
	# we default to disabling them globally to be safe.
	print("[MSS-Login] 🛡️ Disabling Latent Previews (Safe Mode)...")

	class SafeDummyPreviewer:
		def __init__(self, latent_format=None):
			pass

		def check_preview(self, i, preview_every, total_steps):
			if preview_every == 0:
				return False
			return (i % preview_every) == 0

		def decode_latent_to_preview_image(self, preview_format, x0):
			return None  # Return None = No Image

		def close(self):
			pass

	def safe_get_previewer(device, latent_format):
		return SafeDummyPreviewer(latent_format)

	latent_preview.get_previewer = safe_get_previewer


# ----------------------------------------------------------------------------
# PART 3: The Interceptor (Wrapper)
# ----------------------------------------------------------------------------
def install_node_interceptor():
	disable_latent_previews()
	print("[MSS-Login] 🛡️ Installing Node-Level Image Interceptor...")

	try:
		original_save = nodes.SaveImage.save_images
		original_preview = nodes.PreviewImage.save_images
	except AttributeError:
		return

	def intercepted_wrapper(
		self,
		images,
		filename_prefix="ComfyUI",
		prompt=None,
		extra_pnginfo=None,
		mode="unknown",
	):
		is_bad = check_tensor_nsfw(images)

		if is_bad:
			print(f"[mss-login] 🛑 BLOCKED {mode}: Replacing with BLACK SQUARE.")
			black_images = torch.zeros_like(images)
			if mode == "save":
				return original_save(self, black_images, filename_prefix, prompt, extra_pnginfo)
			else:
				return original_preview(self, black_images, filename_prefix, prompt, extra_pnginfo)

		if mode == "save":
			return original_save(self, images, filename_prefix, prompt, extra_pnginfo)
		else:
			return original_preview(self, images, filename_prefix, prompt, extra_pnginfo)

	def save_patch(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
		return intercepted_wrapper(
			self, images, filename_prefix, prompt, extra_pnginfo, mode="save"
		)

	def preview_patch(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
		return intercepted_wrapper(
			self, images, filename_prefix, prompt, extra_pnginfo, mode="preview"
		)

	nodes.SaveImage.save_images = save_patch
	nodes.PreviewImage.save_images = preview_patch

	# Patch animated save nodes when available (SaveAnimatedWEBP, SaveAnimatedPNG).
	# These nodes may not exist in all ComfyUI versions.
	for anim_cls_name in ("SaveAnimatedWEBP", "SaveAnimatedPNG"):
		anim_cls = getattr(nodes, anim_cls_name, None)
		if anim_cls is None or not hasattr(anim_cls, "save_images"):
			continue
		_orig_anim = anim_cls.save_images

		def _make_anim_patch(orig_fn):
			def anim_patch(self, images, *args, **kwargs):
				if check_tensor_nsfw(images):
					print(f"[mss-login] BLOCKED {anim_cls_name}: Replacing with BLACK SQUARE.")
					images = torch.zeros_like(images)
				return orig_fn(self, images, *args, **kwargs)
			return anim_patch

		anim_cls.save_images = _make_anim_patch(_orig_anim)

	# NOTE: ComfyUI may send preview bytes over /ws WebSocket frames without
	# going through /view. This extension mitigates that by disabling latent
	# previews globally and replacing NSFW tensors before save. Custom nodes
	# that bypass SaveImage/PreviewImage are a ComfyUI architectural limitation
	# that cannot be fully closed from an extension.

	print("[MSS-Login] Node Interceptor Active.")


# --- END OF FILE utils/node_interceptor.py ---
