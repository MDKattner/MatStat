"""Tests for config editor helper functions."""

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.qt_app.config_editor_dialog import _parse_config_entries, _write_config_file


class TestParseConfigEntries:
    """Tests for _parse_config_entries — splitting headers from entries."""

    def test_separates_headers_and_entries(self, tmp_path) -> None:
        cfg: Path = tmp_path / "test.config"
        cfg.write_text(
            "# Header comment\n"
            "  # indented comment\n"
            "\n"
            "entry one\n"
            "entry two\n"
        )

        header_lines, entry_lines = _parse_config_entries(cfg)
        assert len(entry_lines) == 2
        assert entry_lines[0] == "entry one"
        assert entry_lines[1] == "entry two"
        assert len(header_lines) == 5  # all 5 lines preserved

    def test_strips_inline_comments(self, tmp_path) -> None:
        cfg: Path = tmp_path / "test.config"
        cfg.write_text("entry one  # with comment\n")

        header_lines, entry_lines = _parse_config_entries(cfg)
        assert entry_lines[0] == "entry one"
        assert "# with comment" not in entry_lines[0]

    def test_empty_file(self, tmp_path) -> None:
        cfg: Path = tmp_path / "empty.config"
        cfg.write_text("")
        header_lines, entry_lines = _parse_config_entries(cfg)
        assert header_lines == []
        assert entry_lines == []

    def test_all_headers_no_entries(self, tmp_path) -> None:
        cfg: Path = tmp_path / "headers.config"
        cfg.write_text("# comment 1\n# comment 2\n\n")
        header_lines, entry_lines = _parse_config_entries(cfg)
        assert len(header_lines) == 3
        assert entry_lines == []

    def test_no_headers(self, tmp_path) -> None:
        cfg: Path = tmp_path / "entries.config"
        cfg.write_text("entry one\nentry two\n")
        header_lines, entry_lines = _parse_config_entries(cfg)
        assert len(header_lines) == 2
        assert len(entry_lines) == 2

    def test_file_not_found_returns_empty(self) -> None:
        header_lines, entry_lines = _parse_config_entries(Path("/nonexistent/config"))
        assert header_lines == []
        assert entry_lines == []

    def test_preserves_indentation(self, tmp_path) -> None:
        cfg: Path = tmp_path / "indent.config"
        cfg.write_text("  entry one\n\tentry two\n")
        header_lines, entry_lines = _parse_config_entries(cfg)
        # header_lines preserves original raw line (with indent)
        assert "  entry one" in header_lines[0]
        assert "\tentry two" in header_lines[1]


class TestWriteConfigFile:
    """Tests for _write_config_file — re-merging headers with modified entries."""

    def test_replaces_entries_preserving_headers(self, tmp_path) -> None:
        cfg: Path = tmp_path / "test.config"
        cfg.write_text("# Header\nentry one\nentry two\n")
        header_lines, _ = _parse_config_entries(cfg)

        _write_config_file(cfg, header_lines, ["new one", "new two"])
        result: str = cfg.read_text()
        lines: list[str] = result.strip().split("\n")
        assert lines[0] == "# Header"
        assert lines[1] == "new one"
        assert lines[2] == "new two"

    def test_preserves_indentation(self, tmp_path) -> None:
        cfg: Path = tmp_path / "indent.config"
        cfg.write_text("  entry one\n")
        header_lines, _ = _parse_config_entries(cfg)

        _write_config_file(cfg, header_lines, ["new entry"])
        result: str = cfg.read_text()
        assert result.rstrip("\n") == "  new entry"

    def test_handles_fewer_new_entries(self, tmp_path) -> None:
        cfg: Path = tmp_path / "fewer.config"
        cfg.write_text("# H\nentry one\nentry two\nentry three\n")
        header_lines, _ = _parse_config_entries(cfg)

        _write_config_file(cfg, header_lines, ["new one", "new two"])
        result: str = cfg.read_text()
        lines: list[str] = result.strip().split("\n")
        assert lines[0] == "# H"
        assert lines[1] == "new one"
        assert lines[2] == "new two"
        assert lines[3] == "entry three"  # unchanged beyond available replacements

    def test_handles_more_new_entries(self, tmp_path) -> None:
        cfg: Path = tmp_path / "more.config"
        cfg.write_text("# H\nentry one\n")
        header_lines, _ = _parse_config_entries(cfg)

        _write_config_file(cfg, header_lines, ["new one", "new two"])
        result: str = cfg.read_text()
        lines: list[str] = result.strip().split("\n")
        assert lines[0] == "# H"
        assert lines[1] == "new one"
        assert lines[2] == "new two"

    def test_no_entries(self, tmp_path) -> None:
        cfg: Path = tmp_path / "no_entries.config"
        cfg.write_text("# just a comment\n")
        header_lines, _ = _parse_config_entries(cfg)

        _write_config_file(cfg, header_lines, [])
        result: str = cfg.read_text()
        assert result.strip() == "# just a comment"
