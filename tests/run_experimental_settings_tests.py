r"""
Standalone tests for experimental settings API helpers (restart reasons + flag keys).

  .venv/bin/python tests/run_experimental_settings_tests.py
"""

from __future__ import annotations

import ast
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADMIN_PATH = os.path.join(_PROJECT_ROOT, "routes", "admin.py")


def _load_restart_helpers():
	"""Load compute_experimental_restart_reasons without importing full admin deps."""
	# Extract pure helper via AST exec of the function + keys constant from source.
	with open(_ADMIN_PATH, "r", encoding="utf-8") as f:
		src = f.read()
	tree = ast.parse(src)
	wanted = {
		"_EXPERIMENTAL_FEATURE_KEYS",
		"_experimental_block_from_cfg",
		"compute_experimental_restart_reasons",
	}
	chunks = []
	for node in tree.body:
		name = None
		if isinstance(node, ast.Assign):
			for t in node.targets:
				if isinstance(t, ast.Name) and t.id in wanted:
					name = t.id
		elif isinstance(node, ast.FunctionDef) and node.name in wanted:
			name = node.name
		if name:
			chunks.append(ast.get_source_segment(src, node))
	ns: dict = {}
	exec("\n\n".join(chunks), ns, ns)  # noqa: S102 — test harness loads audited helpers
	return ns


def run_tests():
	failed = 0
	run = 0

	def ok(cond, msg):
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")
		return cond

	ns = _load_restart_helpers()
	keys = ns["_EXPERIMENTAL_FEATURE_KEYS"]
	block_from = ns["_experimental_block_from_cfg"]
	compute = ns["compute_experimental_restart_reasons"]

	print("TestExperimentalFeatureKeys")
	expected = {
		"mfa",
		"s3",
		"loading_screen",
		"news",
		"model_isolation",
		"tailscale_local_auth",
		"install_other_nodes_deps",
		"login_background",
	}
	ok(set(keys) == expected, f"all 8 keys present (got {set(keys)})")

	print("TestExperimentalBlockFromCfg")
	cfg = {"experimental": {"mfa": True, "s3": 1, "install_other_nodes_deps": True}}
	block = block_from(cfg)
	ok(block["mfa"] is True and block["s3"] is True, "truthy flags normalized")
	ok(block["login_background"] is False, "missing flags default false")
	ok(set(block.keys()) == expected, "block exposes all keys")

	print("TestRestartReasons")
	base = {k: False for k in keys}
	# news only — no restart
	r = compute(True, base, True, {**base, "news": True})
	ok(r == [], f"news-only change needs no restart (got {r})")

	# s3 toggle
	r = compute(True, base, True, {**base, "s3": True})
	ok("S3 storage mount/sync" in r, "enabling s3 requires restart")
	ok(len(r) == 1, "only s3 reason when enabling s3")

	# master toggle while s3 on
	r = compute(False, {**base, "s3": True}, True, {**base, "s3": True})
	ok("S3 storage mount/sync" in r, "master toggle with s3 on requires restart")

	# install_other_nodes_deps on
	r = compute(True, base, True, {**base, "install_other_nodes_deps": True})
	ok("Node dependency auto-install (startup scan)" in r, "enabling node deps requires restart")

	# turning node deps off — no restart
	r = compute(
		True,
		{**base, "install_other_nodes_deps": True},
		True,
		{**base, "install_other_nodes_deps": False},
	)
	ok(r == [], f"disabling node deps needs no restart (got {r})")

	# both s3 and node deps
	r = compute(False, base, True, {**base, "s3": True, "install_other_nodes_deps": True})
	ok(len(r) == 2, f"both restart reasons when enabling s3+deps (got {r})")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
