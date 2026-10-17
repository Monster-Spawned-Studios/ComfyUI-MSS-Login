r"""
Standalone tests for PyTorch install-plan detection (no network, no ComfyUI).

Use the project .venv (see .agent/rules/python-venv.mdc):
  .venv/bin/python tests/run_install_deps_plan_tests.py
"""

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_install_deps():
	path = os.path.join(_PROJECT_ROOT, "utils", "install_deps.py")
	spec = importlib.util.spec_from_file_location("install_deps", path)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	install_deps = _load_install_deps()
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

	print("TestDarwinMetal")
	plan = install_deps.detect_torch_install_plan(system="Darwin", cuda_major=13, env={})
	ok(plan["backend"] == "metal", "macOS uses Metal even if cuda_major is passed")
	ok(plan["extra_index_url"] is None, "macOS uses PyPI (no CUDA/CPU index)")
	ok(plan["requirements_file"] == "requirements_metal.txt", "macOS requirements_metal.txt")

	print("TestCuda130PreferredWhenSupported")
	plan = install_deps.detect_torch_install_plan(system="Linux", cuda_major=13, env={})
	ok(plan["backend"] == "cuda130", "Linux CUDA 13 uses cu130")
	ok(plan["extra_index_url"] == install_deps.PYTORCH_INDEX_CU130, "cu130 index URL")
	plan = install_deps.detect_torch_install_plan(system="Windows", cuda_major=13, env={})
	ok(plan["backend"] == "cuda130", "Windows CUDA 13 uses cu130")

	print("TestCuda128Fallback")
	plan = install_deps.detect_torch_install_plan(system="Linux", cuda_major=12, env={})
	ok(plan["backend"] == "cuda128", "CUDA 12 uses cu128")
	ok(plan["extra_index_url"] == install_deps.PYTORCH_INDEX_CU128, "cu128 index URL")

	print("TestCpuWhenNoCuda")
	plan = install_deps.detect_torch_install_plan(system="Linux", cuda_major=None, env={})
	ok(plan["backend"] == "cpu", "Linux without CUDA uses CPU")
	ok(plan["extra_index_url"] == install_deps.PYTORCH_INDEX_CPU, "CPU index URL")
	ok(plan["requirements_file"] == "requirements_cpu.txt", "CPU requirements file")

	print("TestEnvOverrides")
	plan = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=13, env={"USE_CPU": "1"}
	)
	ok(plan["backend"] == "cpu", "USE_CPU=1 forces CPU even when CUDA 13 is present")
	plan = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=None, env={"USE_CUDA": "1"}
	)
	ok(plan["backend"] == "cuda128", "USE_CUDA=1 without driver falls back to cu128")

	print("TestDarwinIgnoresUseCuda")
	plan = install_deps.detect_torch_install_plan(
		system="Darwin", cuda_major=None, env={"USE_CUDA": "1"}
	)
	ok(plan["backend"] == "metal", "macOS ignores USE_CUDA")

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
