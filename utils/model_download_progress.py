"""Helpers for Comfy node progress aggregation (overall vs per-model)."""

from __future__ import annotations

from typing import Any


def aggregate_progress(
	jobs: list[dict[str, Any]],
	*,
	mode: str = "per_model",
	active_job_id: str | None = None,
) -> tuple[int, int, str]:
	"""
	Return (value, max, label) for Comfy ProgressBar / send_sync.

	mode:
	  - per_model: progress of active_job_id (or first running/queued job)
	  - overall: weighted by known total_bytes across jobs
	"""
	mode_norm = (mode or "per_model").strip().lower()
	if mode_norm not in ("per_model", "overall"):
		mode_norm = "per_model"

	if not jobs:
		return 0, 100, "No download jobs"

	if mode_norm == "overall":
		known_total = 0
		known_done = 0
		unknown_pct_sum = 0.0
		unknown_count = 0
		for job in jobs:
			status = (job.get("status") or "").lower()
			if status == "completed":
				tb = int(job.get("total_bytes") or job.get("bytes_done") or 0)
				if tb > 0:
					known_total += tb
					known_done += tb
				else:
					unknown_pct_sum += 100.0
					unknown_count += 1
				continue
			if status in ("failed", "cancelled"):
				continue
			tb = job.get("total_bytes")
			bd = int(job.get("bytes_done") or 0)
			if tb and int(tb) > 0:
				known_total += int(tb)
				known_done += min(bd, int(tb))
			else:
				unknown_pct_sum += float(job.get("progress_pct") or 0)
				unknown_count += 1
		if known_total > 0:
			base = (known_done / known_total) * 100.0
			if unknown_count:
				# Blend unknown jobs by their progress_pct equally.
				base = (base * known_total + unknown_pct_sum) / (
					known_total + unknown_count * (known_total / max(len(jobs), 1))
				)
			pct = max(0, min(100, int(round(base))))
		elif unknown_count:
			pct = max(0, min(100, int(round(unknown_pct_sum / unknown_count))))
		else:
			pct = 0
		label = f"Overall downloads {pct}%"
		return pct, 100, label

	# per_model
	active = None
	if active_job_id:
		for job in jobs:
			if job.get("job_id") == active_job_id:
				active = job
				break
	if active is None:
		for job in jobs:
			if (job.get("status") or "").lower() == "running":
				active = job
				break
	if active is None and jobs:
		active = jobs[0]
	pct = int(round(float(active.get("progress_pct") or 0)))
	pct = max(0, min(100, pct))
	name = active.get("filename") or active.get("model_version_id") or active.get("repo_id") or "model"
	eta = active.get("eta_seconds")
	eta_s = f", ETA {int(eta)}s" if eta else ""
	bd = int(active.get("bytes_done") or 0)
	tb = active.get("total_bytes")
	size_s = f", {bd}/{tb} bytes" if tb else f", {bd} bytes"
	label = f"{name}: {pct}%{size_s}{eta_s}"
	return pct, 100, label
