"""
Pytest configuration and fixtures
"""

import pytest
import os
import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for testing."""
    return tmp_path


@pytest.fixture
def simple_tree(tmp_path):
    """
    Create a simple test directory tree:

    test_root/
    ├── dir1/
    │   ├── file1.txt
    │   └── file2.dat
    ├── dir2/
    │   └── subdir/
    │       └── file3.log
    └── file0.txt
    """
    root = tmp_path / "test_root"
    root.mkdir()

    # Root file
    (root / "file0.txt").write_text("root file")

    # dir1
    dir1 = root / "dir1"
    dir1.mkdir()
    (dir1 / "file1.txt").write_text("file in dir1")
    (dir1 / "file2.dat").write_bytes(b"binary data")

    # dir2 with subdirectory
    dir2 = root / "dir2"
    dir2.mkdir()
    subdir = dir2 / "subdir"
    subdir.mkdir()
    (subdir / "file3.log").write_text("log entry")

    return root


@pytest.fixture
def tree_with_snapshots(tmp_path):
    """
    Create a tree with .snapshot directories:

    test_root/
    ├── data/
    │   ├── file.txt
    │   └── .snapshot/
    │       └── snapshot_file.txt
    └── .snapshot/
        └── old_data/
    """
    root = tmp_path / "test_root"
    root.mkdir()

    # Data directory
    data_dir = root / "data"
    data_dir.mkdir()
    (data_dir / "file.txt").write_text("current data")

    # Snapshot in data directory
    snapshot = data_dir / ".snapshot"
    snapshot.mkdir()
    (snapshot / "snapshot_file.txt").write_text("old snapshot")

    # Root level snapshot
    root_snapshot = root / ".snapshot"
    root_snapshot.mkdir()
    old_data = root_snapshot / "old_data"
    old_data.mkdir()

    return root


@pytest.fixture
def large_flat_tree(tmp_path):
    """
    Create a tree with many files in one directory.
    Used for performance testing.
    """
    root = tmp_path / "large_flat"
    root.mkdir()

    for i in range(100):
        (root / f"file_{i:04d}.txt").write_text(f"content {i}")

    return root


@pytest.fixture
def deep_tree(tmp_path):
    """
    Create a deep directory tree (10 levels).
    """
    root = tmp_path / "deep_tree"
    current = root
    current.mkdir()

    for level in range(10):
        (current / f"file_level_{level}.txt").write_text(f"level {level}")
        current = current / f"level_{level}"
        current.mkdir()

    return root


@pytest.fixture
def tree_with_symlinks(tmp_path):
    """
    Create a tree with symbolic links.
    """
    root = tmp_path / "symlink_tree"
    root.mkdir()

    # Real files
    real_dir = root / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("real file")

    # Symlink to directory
    (root / "link_to_dir").symlink_to(real_dir)

    # Symlink to file
    (root / "link_to_file.txt").symlink_to(real_dir / "file.txt")

    return root


@pytest.fixture
def tree_with_permissions(tmp_path):
    """
    Create a tree with various permissions.
    """
    root = tmp_path / "perm_tree"
    root.mkdir()

    # Readable directory
    readable = root / "readable"
    readable.mkdir()
    (readable / "file.txt").write_text("can read")

    # Restricted directory (no read permission)
    restricted = root / "restricted"
    restricted.mkdir()
    (restricted / "hidden.txt").write_text("cannot read")
    os.chmod(restricted, 0o000)

    yield root

    # Cleanup: restore permissions
    os.chmod(restricted, 0o755)


@pytest.fixture
def slurm_environment(monkeypatch):
    """Mock SLURM environment."""
    monkeypatch.setenv('SLURM_CPUS_ON_NODE', '16')
    return 16


@pytest.fixture
def filesystem_tree(tmp_path):
    """
    Generate comprehensive test filesystem tree with various file types.
    """
    root = tmp_path / "comprehensive"
    root.mkdir()

    # Create varied structure
    structure = {
        'depth': 3,
        'dirs_per_level': 3,
        'files_per_dir': 5,
    }

    def create_level(parent, depth):
        if depth == 0:
            return

        for d in range(structure['dirs_per_level']):
            dir_path = parent / f"dir_{depth}_{d}"
            dir_path.mkdir()

            # Create files
            for f in range(structure['files_per_dir']):
                file_path = dir_path / f"file_{f}.txt"
                file_path.write_text(f"depth={depth}, dir={d}, file={f}")

            # Recurse
            create_level(dir_path, depth - 1)

    create_level(root, structure['depth'])

    return root
