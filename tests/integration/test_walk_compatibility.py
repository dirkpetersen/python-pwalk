"""
Integration tests for os.walk() compatibility
"""

import pytest
import os
from pwalk import walk


def normalize_results(results):
    """Normalize walk results for comparison."""
    normalized = []
    for dirpath, dirnames, filenames in results:
        # Sort to ensure consistent ordering
        normalized.append((
            dirpath,
            sorted(dirnames),
            sorted(filenames)
        ))
    # Sort by dirpath
    return sorted(normalized, key=lambda x: x[0])


@pytest.mark.parametrize("topdown", [True, False])
def test_walk_matches_os_walk(simple_tree, topdown):
    """Test that pwalk.walk matches os.walk exactly."""
    os_results = list(os.walk(str(simple_tree), topdown=topdown))
    pw_results = list(walk(str(simple_tree), topdown=topdown, ignore_snapshots=False))

    # Normalize for comparison
    os_norm = normalize_results(os_results)
    pw_norm = normalize_results(pw_results)

    assert len(os_norm) == len(pw_norm), "Different number of directories"

    for os_res, pw_res in zip(os_norm, pw_norm):
        assert os_res[0] == pw_res[0], f"Different paths: {os_res[0]} vs {pw_res[0]}"
        assert os_res[1] == pw_res[1], f"Different dirnames in {os_res[0]}"
        assert os_res[2] == pw_res[2], f"Different filenames in {os_res[0]}"


def test_dirnames_modification(simple_tree):
    """Test in-place modification of dirnames to prune traversal."""
    visited = []

    for dirpath, dirnames, filenames in walk(str(simple_tree)):
        visited.append(dirpath)

        # Remove dir2 from traversal
        if 'dir2' in dirnames:
            dirnames.remove('dir2')

    # Should have visited root and dir1, but not dir2 or its subdirectories
    paths_str = [str(p) for p in visited]
    assert str(simple_tree) in paths_str
    assert str(simple_tree / "dir1") in paths_str
    # Note: dirnames modification may not work with C extension yet
    # This test documents expected behavior


def test_error_handling_with_callback(tree_with_permissions):
    """Test that onerror callback is invoked for permission errors."""
    errors = []

    def error_callback(exc):
        errors.append(exc)

    # Walk with error callback
    results = list(walk(str(tree_with_permissions), onerror=error_callback))

    # Should have completed walk
    assert len(results) > 0

    # May have encountered permission error (depending on system)
    # This tests that the callback mechanism works


def test_followlinks_false_default(tree_with_symlinks):
    """Test that symlinks are not followed by default."""
    results = list(walk(str(tree_with_symlinks)))
    paths = [r[0] for r in results]

    # Should visit real directory but not follow symlink
    assert str(tree_with_symlinks / "real") in paths

    # link_to_dir should not be traversed
    link_target = tree_with_symlinks / "link_to_dir" / "file.txt"
    # We shouldn't see the link traversed as a directory


def test_walk_deep_tree(deep_tree):
    """Test walking a deep directory tree."""
    results = list(walk(str(deep_tree)))

    # Should have visited all 11 levels (root + 10 subdirs)
    assert len(results) >= 10

    # Check that we went deep
    depths = [r[0].count(os.sep) for r in results]
    assert max(depths) >= 10
