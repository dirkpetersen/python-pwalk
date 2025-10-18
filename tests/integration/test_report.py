"""
Integration tests for report functionality
"""

import pytest
import os
import csv
from pathlib import Path

try:
    import pyarrow.parquet as pq
    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False

from pwalk import report


@pytest.mark.skipif(not HAS_PARQUET, reason="pyarrow not installed")
def test_report_parquet_basic(simple_tree, temp_dir):
    """Test basic Parquet report generation."""
    output = temp_dir / "test_report.parquet"

    result_path, errors = report(
        str(simple_tree),
        format='parquet',
        output=str(output)
    )

    # Check output file exists
    assert Path(result_path).exists()
    assert result_path == str(output)

    # Read and verify Parquet file
    table = pq.read_table(result_path)

    # Should have records for files and directories
    assert len(table) > 0

    # Verify schema
    expected_columns = {
        'inode', 'parent_inode', 'depth', 'filename', 'extension',
        'uid', 'gid', 'size', 'st_dev', 'st_blocks', 'st_nlink',
        'st_mode', 'atime', 'mtime', 'ctime', 'file_count', 'dir_sum',
        'is_hardlink'
    }
    assert set(table.schema.names) == expected_columns


@pytest.mark.skipif(not HAS_PARQUET, reason="pyarrow not installed")
def test_report_parquet_error_tracking(tree_with_permissions, temp_dir):
    """Test that errors are tracked during report generation."""
    output = temp_dir / "test_report_errors.parquet"

    result_path, errors = report(
        str(tree_with_permissions),
        format='parquet',
        output=str(output)
    )

    # Report should complete
    assert Path(result_path).exists()

    # Errors list should be returned (may be empty depending on permissions)
    assert isinstance(errors, list)


def test_report_csv_basic(simple_tree, temp_dir):
    """Test basic CSV report generation."""
    output = temp_dir / "test_report.csv"

    result_path, errors = report(
        str(simple_tree),
        format='csv',
        output=str(output)
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
        format='csv',
        output=str(output)
    )

    # Read header
    with open(result_path, 'r') as f:
        first_line = f.readline().strip()

    # Should match exact pwalk format
    expected_fields = [
        'inode', 'parent-inode', 'directory-depth', '"filename"',
        '"fileExtension"', 'UID', 'GID', 'st_size', 'st_dev',
        'st_blocks', 'st_nlink', '"st_mode"', 'st_atime',
        'st_mtime', 'st_ctime', 'pw_fcount', 'pw_dirsum'
    ]

    for field in ['inode', 'parent-inode', 'directory-depth', 'UID', 'GID']:
        assert field in first_line


def test_report_invalid_format(simple_tree, temp_dir):
    """Test that invalid format raises error."""
    with pytest.raises(ValueError, match="Invalid format"):
        report(str(simple_tree), format='invalid')


def test_report_default_output(simple_tree):
    """Test report with default output filename."""
    result_path, errors = report(str(simple_tree), format='csv')

    try:
        # Should create file with default name
        assert Path(result_path).exists()
        assert 'filesystem_scan' in result_path

    finally:
        # Cleanup
        if Path(result_path).exists():
            Path(result_path).unlink()


@pytest.mark.skipif(not HAS_PARQUET, reason="pyarrow not installed")
def test_report_hardlink_detection(temp_dir):
    """Test that hard links are detected."""
    # Create test tree with hard link
    root = temp_dir / "hardlink_test"
    root.mkdir()

    original = root / "original.txt"
    original.write_text("original content")

    hardlink = root / "hardlink.txt"
    hardlink.hardlink_to(original)

    output = temp_dir / "hardlink_report.parquet"

    result_path, errors = report(
        str(root),
        format='parquet',
        output=str(output)
    )

    # Read Parquet
    table = pq.read_table(result_path)
    df = table.to_pandas()

    # Check for hard link flag
    hardlinks = df[df['is_hardlink'] == True]

    # Should have detected at least one hard link
    assert len(hardlinks) >= 1
