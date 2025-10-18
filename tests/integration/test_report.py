"""
Integration tests for report functionality
"""

import pytest
import os
import csv
from pathlib import Path

from pwalk import report


def test_report_csv_basic(simple_tree, temp_dir):
    """Test basic CSV report generation."""
    output = temp_dir / "test_report.csv"

    result_path, errors = report(
        str(simple_tree),
        output=str(output),
        compress='none'
    )

    # Check output file exists
    assert Path(result_path).exists()

    # Read and verify CSV
    with open(result_path, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)

        # Verify header matches pwalk format
        assert 'inode' in ','.join(header)
        assert 'parent-inode' in ','.join(header)
        assert 'filename' in ','.join(header)

        # Count rows
        rows = list(reader)
        assert len(rows) > 0


def test_report_csv_format_compatibility(simple_tree, temp_dir):
    """Test that CSV format is compatible with John Dey's pwalk."""
    output = temp_dir / "test_report_compat.csv"

    result_path, errors = report(
        str(simple_tree),
        output=str(output),
        compress='none'
    )

    # Read header
    with open(result_path, 'r') as f:
        first_line = f.readline().strip()

    # Should match exact pwalk format
    for field in ['inode', 'parent-inode', 'directory-depth', 'UID', 'GID']:
        assert field in first_line


def test_report_invalid_compress(simple_tree, temp_dir):
    """Test that invalid compress option raises error."""
    with pytest.raises(ValueError, match="Invalid compress"):
        report(str(simple_tree), compress='invalid')


def test_report_default_output(simple_tree):
    """Test report with default output filename."""
    result_path, errors = report(str(simple_tree), compress='none')

    try:
        # Should create file with default name
        assert Path(result_path).exists()
        assert 'scan.csv' in result_path

    finally:
        # Cleanup
        if Path(result_path).exists():
            Path(result_path).unlink()


def test_report_with_compression(temp_dir):
    """Test report with zstd compression if available."""
    import _pwalk_core
    
    if not _pwalk_core.HAS_ZSTD:
        pytest.skip("zstd not available")

    # Create test tree
    root = temp_dir / "compress_test"
    root.mkdir()
    (root / "file.txt").write_text("test")

    output = temp_dir / "compressed.csv"
    result_path, errors = report(str(root), output=str(output), compress='zstd')

    assert Path(result_path).exists()
    # Compressed file should exist
    assert result_path == str(output)
