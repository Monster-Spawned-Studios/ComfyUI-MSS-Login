# utils/reactor_sfw_intercept.py
#
# Per-user Reactor SFW bypass:
# - Dynamically loads comfyui-reactor/scripts/reactor_sfw.py from disk
# - Adds comfyui-reactor root to sys.path so reactor_utils and scripts.* imports work
# - Monkey-patches nsfw_image()
# - Reads sfw_check from users_db using current_username_var
# - Never crashes the extension if Reactor is missing

import importlib.util
import os
import sys

from ..globals import users_db, current_username_var


def _load_reactor_module():
	try:
		# This file: .../custom_nodes/ComfyUI-mss_login/utils/reactor_sfw_intercept.py
		# base -> .../custom_nodes/ComfyUI-mss_login
		base = os.path.dirname(os.path.dirname(__file__))

		# reactor_root -> .../custom_nodes/comfyui-reactor
		reactor_root = os.path.abspath(os.path.join(base, "..", "comfyui-reactor"))
		scripts_dir = os.path.join(reactor_root, "scripts")
		reactor_path = os.path.join(scripts_dir, "reactor_sfw.py")

		if not os.path.exists(reactor_path):
			print("[mss_login] Reactor SFW script not found at:", reactor_path)
			return None

		# Add the Reactor root so:
		#   from reactor_utils import download
		#   from scripts.reactor_logger import logger
		# both work as in the original environment.
		if reactor_root not in sys.path:
			sys.path.insert(0, reactor_root)
			print("[mss_login] Added Reactor root to sys.path:", reactor_root)

		spec = importlib.util.spec_from_file_location("reactor_sfw", reactor_path)
		if spec is None or spec.loader is None:
			print("[mss_login] Could not create spec for reactor_sfw.")
			return None

		mod = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(mod)
		return mod

	except Exception as e:
		print("[mss_login] Failed to load reactor_sfw:", e)
		return None


def _apply_patch():
	reactor_sfw_mod = _load_reactor_module()
	if reactor_sfw_mod is None:
		print("[mss_login] Reactor not found; SFW intercept is disabled.")
		return

	if not hasattr(reactor_sfw_mod, "nsfw_image"):
		print("[mss_login] reactor_sfw.nsfw_image not found; cannot patch.")
		return

	original_nsfw_image = reactor_sfw_mod.nsfw_image

	def nsfw_image_patched(img_data, model_path: str):
		"""
		Wrapper around Reactor's nsfw_image().
		If the current user has sfw_check == False, we bypass detection
		and always return False ("not NSFW").
		Otherwise, call the original implementation.
		"""
		try:
			username = current_username_var.get(None)
		except LookupError:
			username = None

		if username:
			_, rec = users_db.get_user(username)
			if rec and rec.get("sfw_check", True) is False:
				# SFW disabled for this user → pretend everything is safe
				return False

		# Default behavior: let Reactor do its thing
		return original_nsfw_image(img_data, model_path)

	reactor_sfw_mod.nsfw_image = nsfw_image_patched
	print("[mss_login] Reactor SFW intercept installed successfully.")


# Run the patch at import time, but never crash the extension if it fails.
try:
	_apply_patch()
except Exception as e:
	print(f"[mss_login] Unexpected error while applying Reactor SFW intercept: {e}")
