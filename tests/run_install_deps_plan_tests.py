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

	print("TestDarwinAppleSiliconMetal")
	plan_arm = install_deps.detect_torch_install_plan(system="Darwin", machine="arm64", env={})
	ok(plan_arm["backend"] == "metal", "macOS Apple Silicon (arm64) uses Metal")
	ok(
		plan_arm["requirements_file"] == "requirements_metal.txt",
		"Apple Silicon uses requirements_metal.txt",
	)

	print("TestDarwinIntelCpu")
	plan_intel = install_deps.detect_torch_install_plan(system="Darwin", machine="x86_64", env={})
	ok(plan_intel["backend"] == "cpu", "macOS Intel (x86_64) uses CPU mode")
	ok(
		plan_intel["requirements_file"] == "requirements_cpu.txt",
		"Intel Mac uses requirements_cpu.txt",
	)
	ok(
		plan_intel["extra_index_url"] is None,
		"Intel Mac on Darwin uses PyPI index (not linux/win cpu index)",
	)

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

	print("TestRocmAndXpuDetection")
	plan_rocm = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=None, rocm_available=True, env={}
	)
	ok(plan_rocm["backend"] == "rocm", "Linux with ROCm uses rocm backend")
	ok(
		plan_rocm["extra_index_url"] == install_deps.PYTORCH_INDEX_ROCM,
		"ROCm uses PyTorch ROCm index",
	)
	plan_xpu = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=None, rocm_available=False, xpu_available=True, env={}
	)
	ok(plan_xpu["backend"] == "xpu", "Linux with Intel XPU uses xpu backend")
	ok(
		plan_xpu["extra_index_url"] == install_deps.PYTORCH_INDEX_XPU,
		"Intel XPU uses PyTorch XPU index",
	)
	plan_dev_rocm = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=None, env={"DEVICE_TYPE": "rocm"}
	)
	ok(plan_dev_rocm["backend"] == "rocm", "DEVICE_TYPE=rocm forces rocm backend")
	plan_dev_xpu = install_deps.detect_torch_install_plan(
		system="Linux", cuda_major=None, env={"DEVICE_TYPE": "xpu"}
	)
	ok(plan_dev_xpu["backend"] == "xpu", "DEVICE_TYPE=xpu forces xpu backend")

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
