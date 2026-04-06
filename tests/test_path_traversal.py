r"""
Tests for path traversal prevention (utils.path_safety and related usage).

Run with the project .venv (see .agent/rules/python-venv.mdc):
  .\.venv\Scripts\python -m pytest tests/test_path_traversal.py -v
  # or use the standalone runner (no ComfyUI deps):
  .\.venv\Scripts\python tests/run_path_traversal_tests.py
"""

import importlib.util
import os
import sys
import tempfile

# Load path_safety without importing the rest of utils (avoids ComfyUI/server deps)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path_safety_path = os.path.join(_PROJECT_ROOT, "utils", "path_safety.py")
_spec = importlib.util.spec_from_file_location("path_safety", _path_safety_path)
_path_safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_path_safety)

is_safe_filename = _path_safety.is_safe_filename
is_safe_folder_segment = _path_safety.is_safe_folder_segment
safe_basename = _path_safety.safe_basename
resolve_path_under = _path_safety.resolve_path_under
path_under = _path_safety.path_under


class TestIsSafeFilename:
    """Test is_safe_filename rejects path traversal and path components."""

    def test_allows_simple_name(self):
        assert is_safe_filename("image.png") is True
        assert is_safe_filename("workflow.json") is True
        assert is_safe_filename("a") is True

    def test_rejects_double_dot(self):
        assert is_safe_filename("..") is False
        assert is_safe_filename("a..b") is False
        assert is_safe_filename("..file") is False

    def test_rejects_slashes(self):
        assert is_safe_filename("a/b") is False
        assert is_safe_filename("/etc/passwd") is False
        assert is_safe_filename("sub/file.json") is False

    def test_rejects_backslashes(self):
        assert is_safe_filename("a\\b") is False
        assert is_safe_filename("..\\etc\\passwd") is False

    def test_rejects_empty_or_none(self):
        assert is_safe_filename("") is False
        assert is_safe_filename("   ") is False
        assert is_safe_filename(None) is False

    def test_rejects_non_string(self):
        assert is_safe_filename(123) is False


class TestIsSafeFolderSegment:
    """Test is_safe_folder_segment for single path segments (e.g. model folder names)."""

    def test_allows_safe_segments(self):
        assert is_safe_folder_segment("checkpoints") is True
        assert is_safe_folder_segment("loras") is True
        assert is_safe_folder_segment("my_folder") is True

    def test_rejects_traversal(self):
        assert is_safe_folder_segment("..") is False
        assert is_safe_folder_segment("a/b") is False
        assert is_safe_folder_segment("a\\b") is False

    def test_rejects_absolute(self):
        assert is_safe_folder_segment("/etc") is False
        assert is_safe_folder_segment("C:\\Windows") is False


class TestSafeBasename:
    """Test safe_basename extracts only last segment when safe."""

    def test_returns_basename_when_safe(self):
        assert safe_basename("file.json") == "file.json"
        assert safe_basename("a/b/file.json") == "file.json"
        assert safe_basename("a\\b\\file.json") == "file.json"

    def test_returns_none_when_unsafe(self):
        assert safe_basename("..") is None
        assert safe_basename("a/..") is None
        assert safe_basename("") is None
        assert safe_basename(None) is None


class TestResolvePathUnder:
    """Test resolve_path_under contains paths under base_dir."""

    def test_returns_path_when_under_base(self):
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "sub")
            os.makedirs(sub, exist_ok=True)
            f = os.path.join(sub, "file.txt")
            open(f, "w").close()
            resolved = resolve_path_under(base, "sub/file.txt")
            assert resolved is not None
            assert os.path.samefile(resolved, f) or resolved == os.path.normpath(f)

    def test_rejects_double_dot(self):
        with tempfile.TemporaryDirectory() as base:
            assert resolve_path_under(base, "..") is None
            assert resolve_path_under(base, "sub/../etc") is None
            assert resolve_path_under(base, "a/../../etc/passwd") is None

    def test_rejects_leading_slash(self):
        with tempfile.TemporaryDirectory() as base:
            assert resolve_path_under(base, "/etc/passwd") is None

    def test_returns_none_for_nonexistent_base(self):
        assert resolve_path_under("/nonexistent/base/dir", "file.txt") is None

    def test_returns_none_for_empty_or_invalid_input(self):
        with tempfile.TemporaryDirectory() as base:
            assert resolve_path_under("", "file.txt") is None
            assert resolve_path_under(base, None) is None
            assert resolve_path_under(base, 123) is None


class TestPathUnder:
    """Test path_under helper for resolved paths."""

    def test_true_when_under(self):
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "sub")
            os.makedirs(sub, exist_ok=True)
            assert path_under(sub, base) is True
            assert path_under(os.path.join(sub, "file.txt"), base) is True

    def test_true_when_equal(self):
        with tempfile.TemporaryDirectory() as base:
            assert path_under(base, base) is True

    def test_false_when_escapes(self):
        with tempfile.TemporaryDirectory() as base:
            other = tempfile.mkdtemp()
            try:
                assert path_under(other, base) is False
                assert path_under(os.path.join(base, "..", os.path.basename(other)), base) is False
            finally:
                os.rmdir(other)
