# --- START OF FILE utils/cpe_browser.py ---
"""Embedded Playwright UI→API workflow converter for Comfy Portal compatibility.

Adapted from comfy-portal-endpoint's browser.py (MIT © 2025 Shun.L):
https://github.com/ShunL12324/comfy-portal-endpoint

Differences from upstream CPE:
- No runtime pip / Chromium auto-install (Comfy registry rule).
- Injects jwt_token cookie + Authorization Bearer so MSS-Login auth allows
  the ComfyUI frontend to boot on loopback without a public login-wall bypass.
"""

from __future__ import annotations

import asyncio
import atexit
import enum
import logging
import os
import signal
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("mss-login.cpe_browser")

DEFAULT_POOL_SIZE = 2


class BrowserStatus(enum.Enum):
	NOT_INSTALLED = "not_installed"
	NOT_INITIALIZED = "not_initialized"
	INITIALIZING = "initializing"
	READY = "ready"
	ERROR = "error"


class EmbeddedBrowserManager:
	"""Headless Chromium page pool with authenticated ComfyUI frontend sessions."""

	def __init__(self, pool_size: int = DEFAULT_POOL_SIZE):
		self._status = BrowserStatus.NOT_INITIALIZED
		self._error_message: str | None = None
		self._playwright = None
		self._browser = None
		self._driver_pid: int | None = None
		self._contexts: list = []
		self._page_pool: asyncio.Queue | None = None
		self._pool_size = pool_size
		self._init_lock: asyncio.Lock | None = None
		self._warned_legacy_fallback = False
		self._auth_token: str | None = None
		atexit.register(self._sync_cleanup)

	@property
	def status(self) -> BrowserStatus:
		return self._status

	@property
	def error_message(self) -> str | None:
		return self._error_message

	def _get_init_lock(self) -> asyncio.Lock:
		if self._init_lock is None:
			self._init_lock = asyncio.Lock()
		return self._init_lock

	@staticmethod
	def playwright_available() -> bool:
		try:
			import playwright

			return True
		except ImportError:
			return False

	def _get_comfyui_url(self) -> str:
		from server import PromptServer

		server_instance = PromptServer.instance
		address = server_instance.address or "127.0.0.1"
		port = server_instance.port
		if address in ("0.0.0.0", "::", "[::]"):
			address = "127.0.0.1"
		return f"http://{address}:{port}"

	async def _wait_for_comfyui_ready(self, page) -> None:
		await page.wait_for_function("() => typeof window.LGraph !== 'undefined'", timeout=30000)
		from playwright.async_api import TimeoutError as PlaywrightTimeoutError

		try:
			await page.wait_for_function(
				"""() => typeof window.app?.loadGraphData === 'function'
					&& typeof window.app?.graphToPrompt === 'function'""",
				timeout=60000,
			)
		except PlaywrightTimeoutError:
			if not self._warned_legacy_fallback:
				self._warned_legacy_fallback = True
				logger.warning(
					"[MSS-Login CPE] Native graphToPrompt unavailable; "
					"subgraph conversion may be incomplete."
				)
			await page.wait_for_function(
				"""() => typeof window.app?.graphToPrompt === 'function'
					|| typeof window.LiteGraph !== 'undefined'""",
				timeout=30000,
			)
		await page.wait_for_function(
			"""() => {
				if (typeof LiteGraph === 'undefined') return false;
				const types = LiteGraph.registered_node_types;
				return types && Object.keys(types).length > 0;
			}""",
			timeout=60000,
		)

	async def _create_page(self, comfyui_url: str, auth_token: str | None):
		headers = {}
		if auth_token:
			headers["Authorization"] = f"Bearer {auth_token}"
		if headers:
			context = await self._browser.new_context(extra_http_headers=headers)
		else:
			context = await self._browser.new_context()
		if auth_token:
			parsed = urlparse(comfyui_url)
			cookie_url = f"{parsed.scheme}://{parsed.netloc}/"
			await context.add_cookies(
				[
					{
						"name": "jwt_token",
						"value": auth_token,
						"url": cookie_url,
						"httpOnly": False,
						"secure": parsed.scheme == "https",
						"sameSite": "Lax",
					}
				]
			)
		page = await context.new_page()
		await page.goto(comfyui_url, timeout=60000, wait_until="domcontentloaded")
		await self._wait_for_comfyui_ready(page)
		self._contexts.append(context)
		return page

	async def initialize(self, auth_token: str | None = None) -> None:
		if not self.playwright_available():
			self._status = BrowserStatus.NOT_INSTALLED
			self._error_message = (
				"Playwright is not installed. "
				"Run: uv sync --group cpe-convert && playwright install chromium"
			)
			raise RuntimeError(self._error_message)

		if (
			self._status == BrowserStatus.READY
			and self._page_pool is not None
			and auth_token
			and auth_token == self._auth_token
		):
			return

		init_lock = self._get_init_lock()
		async with init_lock:
			if (
				self._status == BrowserStatus.READY
				and self._page_pool is not None
				and auth_token
				and auth_token == self._auth_token
			):
				return

			# Re-init when token changes so pages carry the correct session
			if self._browser is not None:
				await self._cleanup()

			self._status = BrowserStatus.INITIALIZING
			self._error_message = None
			self._auth_token = auth_token

			try:
				from playwright.async_api import async_playwright
			except ImportError:
				self._status = BrowserStatus.NOT_INSTALLED
				self._error_message = (
					"Playwright is not installed. "
					"Run: uv sync --group cpe-convert && playwright install chromium"
				)
				raise RuntimeError(self._error_message)

			try:
				comfyui_url = self._get_comfyui_url()
				logger.info("[MSS-Login CPE] Initializing headless browser at %s", comfyui_url)
				self._playwright = await async_playwright().start()
				self._browser = await self._playwright.chromium.launch(
					headless=True,
					args=[
						"--no-sandbox",
						"--disable-gpu",
						"--disable-dev-shm-usage",
						"--disable-extensions",
						"--disable-background-networking",
						"--disable-default-apps",
						"--disable-sync",
						"--disable-translate",
						"--no-first-run",
						"--mute-audio",
					],
				)
				try:
					transport = self._playwright._connection._transport
					self._driver_pid = transport._proc.pid
				except Exception:
					self._driver_pid = None

				self._page_pool = asyncio.Queue()
				self._contexts = []
				for _ in range(self._pool_size):
					page = await self._create_page(comfyui_url, auth_token)
					await self._page_pool.put(page)

				self._status = BrowserStatus.READY
				logger.info("[MSS-Login CPE] Headless browser ready (%d pages)", self._pool_size)
			except Exception as exc:
				self._status = BrowserStatus.ERROR
				self._error_message = f"Failed to initialize headless browser: {exc}"
				logger.error("[MSS-Login CPE] %s", self._error_message)
				await self._cleanup()
				raise RuntimeError(self._error_message) from exc

	async def _cleanup(self) -> None:
		try:
			if self._page_pool is not None:
				while not self._page_pool.empty():
					try:
						page = self._page_pool.get_nowait()
						await page.close()
					except Exception:
						pass
				self._page_pool = None
			for ctx in self._contexts:
				try:
					await ctx.close()
				except Exception:
					pass
			self._contexts = []
			if self._browser is not None:
				try:
					await self._browser.close()
				except Exception:
					pass
				self._browser = None
			if self._playwright is not None:
				try:
					await self._playwright.stop()
				except Exception:
					pass
				self._playwright = None
			self._driver_pid = None
			self._auth_token = None
		except Exception as exc:
			logger.error("[MSS-Login CPE] Cleanup error: %s", exc)

	def _sync_cleanup(self) -> None:
		if self._driver_pid is not None:
			try:
				os.kill(self._driver_pid, signal.SIGTERM)
			except ProcessLookupError:
				pass
			except Exception:
				pass

	async def convert_workflow(
		self, workflow_data: dict[str, Any], *, auth_token: str | None = None
	) -> dict[str, Any]:
		if not isinstance(workflow_data, dict):
			raise RuntimeError("Workflow data must be a JSON object")
		await self.initialize(auth_token=auth_token)
		assert self._page_pool is not None
		page = await self._page_pool.get()
		try:
			result = await self._do_convert(page, workflow_data)
			await self._page_pool.put(page)
			return result
		except Exception as first_error:
			logger.warning(
				"[MSS-Login CPE] Conversion failed, retrying with reload: %s", first_error
			)
			try:
				result = await self._do_convert(page, workflow_data, reload=True)
				await self._page_pool.put(page)
				return result
			except Exception as retry_error:
				await self._replace_page(page, auth_token)
				self._error_message = f"Conversion failed after retry: {retry_error}"
				raise RuntimeError(self._error_message) from retry_error

	async def _replace_page(self, broken_page, auth_token: str | None) -> None:
		try:
			await broken_page.close()
		except Exception:
			pass
		try:
			comfyui_url = self._get_comfyui_url()
			new_page = await self._create_page(comfyui_url, auth_token)
			assert self._page_pool is not None
			await self._page_pool.put(new_page)
		except Exception as exc:
			logger.error("[MSS-Login CPE] Failed to replace page: %s", exc)
			self._status = BrowserStatus.ERROR
			self._error_message = "Page pool degraded; will re-initialize on next request"

	async def _do_convert(self, page, workflow_data: dict, reload: bool = False) -> dict[str, Any]:
		if reload:
			await page.reload(wait_until="domcontentloaded", timeout=30000)
			await self._wait_for_comfyui_ready(page)

		result = await page.evaluate(
			"""async (workflowData) => {
				try {
					let output;
					if (window.app?.loadGraphData && window.app?.graphToPrompt) {
						await window.app.loadGraphData(workflowData);
						output = (await window.app.graphToPrompt()).output;
					} else if (window.app?.graphToPrompt && window.LGraph) {
						const graph = new window.LGraph();
						graph.configure(workflowData, false);
						if (window.app.graph) {
							window.app.graph.clear();
							window.app.loadGraphData(workflowData);
						}
						output = (await window.app.graphToPrompt()).output;
					} else {
						return {
							success: false,
							error: 'ComfyUI graphToPrompt API not available in page',
						};
					}
					const known = new Set(
						(workflowData.nodes || []).map(n => String(n.id))
					);
					if (known.size) {
						const foreign = Object.keys(output).filter(
							id => !known.has(String(id).split(':')[0])
						);
						if (foreign.length) {
							return {
								success: false,
								error: 'Converted graph does not match the requested workflow'
									+ ' (unexpected nodes: ' + foreign.slice(0, 5).join(', ') + ')',
							};
						}
					}
					return { success: true, workflow: output };
				} catch (e) {
					return { success: false, error: e.message || String(e) };
				}
			}""",
			workflow_data,
		)
		if not result.get("success"):
			raise RuntimeError(f"JS conversion error: {result.get('error', 'Unknown error')}")
		return result["workflow"]


_embedded_manager: EmbeddedBrowserManager | None = None


def get_embedded_browser_manager() -> EmbeddedBrowserManager:
	global _embedded_manager
	if _embedded_manager is None:
		_embedded_manager = EmbeddedBrowserManager()
		if not EmbeddedBrowserManager.playwright_available():
			_embedded_manager._status = BrowserStatus.NOT_INSTALLED
			_embedded_manager._error_message = (
				"Playwright is not installed. "
				"Run: uv sync --group cpe-convert && playwright install chromium"
			)
	return _embedded_manager


# --- END OF FILE utils/cpe_browser.py ---
