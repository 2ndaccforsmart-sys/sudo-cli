"""Tests for grep command — exit code handling and fallback logic."""

import subprocess
from unittest.mock import patch, MagicMock

from sudo.commands.grep import _try_rg, _fallback_grep, run_grep


class TestTryRg:
    """Test ripgrep detection and exit code handling."""

    def _make_args(self, pattern="test", context=0, max_matches=5, files=False):
        args = MagicMock()
        args.pattern = pattern
        args.context = context
        args.max_matches = max_matches
        args.files = files
        return args

    @patch("sudo.commands.grep.subprocess.run")
    def test_rg_not_installed_returns_1(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        args = self._make_args()
        result = _try_rg(args)
        assert result == 1  # 1 = rg not installed, triggers fallback

    @patch("sudo.commands.grep.subprocess.run")
    def test_rg_no_matches_returns_0(self, mock_run):
        """ripgrep exit 1 = no matches, should NOT trigger fallback."""
        # First call: rg --version (succeeds)
        # Second call: actual search (exit 1 = no matches)
        mock_run.side_effect = [
            MagicMock(returncode=0),  # version check
            MagicMock(returncode=1, stdout="", stderr=""),  # no matches
        ]
        args = self._make_args()
        result = _try_rg(args)
        assert result == 0  # 0 = success, no fallback

    @patch("sudo.commands.grep.subprocess.run")
    def test_rg_success_returns_0(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # version check
            MagicMock(returncode=0, stdout="file.py:1:match", stderr=""),  # matches found
        ]
        args = self._make_args()
        result = _try_rg(args)
        assert result == 0

    @patch("sudo.commands.grep.subprocess.run")
    def test_rg_error_returns_2(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # version check
            MagicMock(returncode=2, stdout="", stderr="error"),  # actual error
        ]
        args = self._make_args()
        result = _try_rg(args)
        assert result == 2

    @patch("sudo.commands.grep.subprocess.run")
    def test_rg_timeout_returns_2(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # version check
            subprocess.TimeoutExpired(cmd="rg", timeout=30),  # timeout
        ]
        args = self._make_args()
        result = _try_rg(args)
        assert result == 2


class TestRunGrep:
    @patch("sudo.commands.grep._try_rg")
    @patch("sudo.commands.grep._fallback_grep")
    def test_fallback_when_rg_missing(self, mock_fallback, mock_try):
        mock_try.return_value = 1  # rg not installed
        args = MagicMock()
        run_grep(args)
        mock_fallback.assert_called_once_with(args)

    @patch("sudo.commands.grep._try_rg")
    @patch("sudo.commands.grep._fallback_grep")
    def test_no_fallback_when_rg_succeeds(self, mock_fallback, mock_try):
        mock_try.return_value = 0  # success
        args = MagicMock()
        run_grep(args)
        mock_fallback.assert_not_called()
