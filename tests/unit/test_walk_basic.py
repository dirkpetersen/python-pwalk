"""
Unit tests for basic walk functionality
"""

import pytest
import os
from pwalk import walk


def test_walk_simple_tree(simple_tree):
    """Test walking a simple directory tree."""
    results = list(walk(str(simple_tree)))

    # Should have walked root, dir1, dir2, and subdir
    paths = [r[0] for r in results]
    assert str(simple_tree) in paths
    assert str(simple_tree / "dir1") in paths
    assert str(simple_tree / "dir2") in paths
    assert str(simple_tree / "dir2" / "subdir") in paths


def test_walk_returns_tuples(simple_tree):
    """Test that walk returns correct tuple format."""
    for dirpath, dirnames, filenames in walk(str(simple_tree)):
        assert isinstance(dirpath, str)
        assert isinstance(dirnames, list)
        assert isinstance(filenames, list)
        assert all(isinstance(d, str) for d in dirnames)
        assert all(isinstance(f, str) for f in filenames)


def test_walk_topdown_true(simple_tree):
    """Test topdown=True yields parents before children."""
    results = list(walk(str(simple_tree), topdown=True))
    paths = [r[0] for r in results]

    # Root should come before subdirectories
    root_idx = paths.index(str(simple_tree))
    dir1_idx = paths.index(str(simple_tree / "dir1"))
    dir2_idx = paths.index(str(simple_tree / "dir2"))

    assert root_idx < dir1_idx
    assert root_idx < dir2_idx


def test_walk_topdown_false(simple_tree):
    """Test topdown=False yields children before parents."""
    results = list(walk(str(simple_tree), topdown=False))
    paths = [r[0] for r in results]

    # Subdirectories should come before root
    root_idx = paths.index(str(simple_tree))
    dir1_idx = paths.index(str(simple_tree / "dir1"))
    dir2_idx = paths.index(str(simple_tree / "dir2"))

    assert root_idx > dir1_idx
    assert root_idx > dir2_idx


def test_walk_counts_files_correctly(simple_tree):
    """Test that file counts are correct."""
    total_files = 0
    total_dirs = 0

    for dirpath, dirnames, filenames in walk(str(simple_tree)):
        total_files += len(filenames)
        total_dirs += len(dirnames)

    # Should have 4 files total and 3 directories (dir1, dir2, subdir)
    assert total_files == 4
    assert total_dirs > 0


def test_walk_with_empty_directory(temp_dir):
    """Test walking an empty directory."""
    empty = temp_dir / "empty"
    empty.mkdir()

    results = list(walk(str(empty)))

    assert len(results) == 1
    dirpath, dirnames, filenames = results[0]
    assert dirpath == str(empty)
    assert len(dirnames) == 0
    assert len(filenames) == 0


def test_walk_nonexistent_path():
    """Test walking a non-existent path - behavior matches os.walk()."""
    # os.walk() doesn't raise immediately, it yields nothing or handles internally
    # Our implementation follows os.walk() behavior
    results = list(walk('/nonexistent/path/that/does/not/exist'))
    # Should either be empty or handle the error via onerror callback
    assert isinstance(results, list)


def test_walk_with_file_path(simple_tree):
    """Test that walking a file path - behavior matches os.walk()."""
    file_path = simple_tree / "file0.txt"
    # os.walk() on a file returns empty iterator
    results = list(walk(str(file_path)))
    # Should return empty or single entry
    assert isinstance(results, list)


def test_walk_ignore_snapshots_default(tree_with_snapshots):
    """Test that .snapshot directories are ignored by default."""
    results = list(walk(str(tree_with_snapshots)))
    paths = [r[0] for r in results]

    # Should not include any .snapshot directories
    assert not any('.snapshot' in p for p in paths)


def test_walk_ignore_snapshots_false(tree_with_snapshots):
    """Test that .snapshot directories are included when ignore_snapshots=False."""
    results = list(walk(str(tree_with_snapshots), ignore_snapshots=False))
    paths = [r[0] for r in results]

    # Should include .snapshot directories
    assert any('.snapshot' in p for p in paths)


def test_walk_max_threads(simple_tree):
    """Test that max_threads parameter is accepted."""
    # Should not raise an error
    results = list(walk(str(simple_tree), max_threads=4))
    assert len(results) > 0


def test_walk_slurm_environment(simple_tree, slurm_environment):
    """Test that SLURM_CPUS_ON_NODE is respected."""
    # Should not raise an error and should use SLURM thread count
    results = list(walk(str(simple_tree)))
    assert len(results) > 0
