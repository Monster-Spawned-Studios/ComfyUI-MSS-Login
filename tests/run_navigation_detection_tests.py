r"""
Standalone runner for browser navigation detection tests.

Use the project .venv:
  ./.venv/bin/python tests/run_navigation_detection_tests.py
"""

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_module_path = os.path.join(_PROJECT_ROOT, "utils", "request_navigation.py")
_spec = importlib.util.spec_from_file_location("request_navigation", _module_path)
_request_navigation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_request_navigation)

is_browser_navigation = _request_navigation.is_browser_navigation


class FakeRequest:
    def __init__(self, headers=None, content_type=""):
        self.headers = headers or {}
        self.content_type = content_type


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

    print("TestNavigationSignals")
    ok(
        is_browser_navigation(FakeRequest(headers={"Sec-Fetch-Mode": "navigate"})),
        "Sec-Fetch-Mode=navigate is browser navigation",
    )
    ok(
        not is_browser_navigation(
            FakeRequest(headers={"Sec-Fetch-Mode": "cors", "Accept": "text/html"})
        ),
        "Sec-Fetch-Mode=cors is not navigation",
    )

    print("TestApiSafeFallbackWhenSecFetchModeMissing")
    ok(
        not is_browser_navigation(FakeRequest(content_type="application/json")),
        "JSON content type is not navigation",
    )
    ok(
        not is_browser_navigation(FakeRequest(headers={"Accept": "application/json"})),
        "JSON Accept is not navigation",
    )
    ok(
        not is_browser_navigation(FakeRequest(headers={"Accept": "*/*"})),
        "Generic Accept */* is not navigation",
    )
    ok(
        not is_browser_navigation(FakeRequest()),
        "Missing Accept and content type is not navigation",
    )

    print("TestHtmlFallback")
    ok(
        is_browser_navigation(FakeRequest(headers={"Accept": "text/html"})),
        "HTML Accept is navigation",
    )
    ok(
        is_browser_navigation(
            FakeRequest(headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        ),
        "Browser-like HTML Accept list is navigation",
    )

    print()
    if failed:
        print(f"Result: {failed} failed, {run - failed} passed, {run} total")
        sys.exit(1)
    print(f"Result: all {run} tests passed")
    sys.exit(0)


if __name__ == "__main__":
    run_tests()
