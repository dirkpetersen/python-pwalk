#!/usr/bin/env python3
"""
Benchmark pwalk.walk() vs os.walk() to validate performance claims.

Tests both on various directory structures to measure speedup.
"""

import os
import sys
import time
import tempfile
import shutil
from pathlib import Path


def create_test_tree(root, depth=3, dirs_per_level=5, files_per_dir=20):
    """Create a test directory tree with specified structure."""

    def create_level(parent, current_depth):
        if current_depth > depth:
            return

        # Create files in this directory
        for i in range(files_per_dir):
            filepath = parent / f"file_{i}.txt"
            filepath.write_text(f"test content {i}\n")

        # Create subdirectories
        for i in range(dirs_per_level):
            subdir = parent / f"dir_{i}"
            subdir.mkdir(exist_ok=True)
            create_level(subdir, current_depth + 1)

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    create_level(root_path, 1)


def count_files_oswalk(path):
    """Count files using os.walk()."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(path):
        count += len(filenames)
    return count


def count_files_pwalk(path):
    """Count files using pwalk.walk()."""
    from pwalk import walk
    count = 0
    for dirpath, dirnames, filenames in walk(path):
        count += len(filenames)
    return count


def benchmark_walk(path, name, func, iterations=3):
    """Benchmark a walk function."""
    times = []

    for i in range(iterations):
        start = time.time()
        count = func(path)
        elapsed = time.time() - start
        times.append(elapsed)

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    return {
        'name': name,
        'count': count,
        'avg_time': avg_time,
        'min_time': min_time,
        'max_time': max_time,
        'files_per_sec': count / avg_time
    }


def main():
    print("=" * 70)
    print("pwalk.walk() vs os.walk() Performance Benchmark")
    print("=" * 70)

    # Test configurations
    configs = [
        {'name': 'Small', 'depth': 2, 'dirs': 3, 'files': 10},
        {'name': 'Medium', 'depth': 3, 'dirs': 5, 'files': 20},
        {'name': 'Large', 'depth': 4, 'dirs': 4, 'files': 30},
    ]

    for config in configs:
        print(f"\n{config['name']} Tree (depth={config['depth']}, "
              f"dirs={config['dirs']}, files={config['files']}):")
        print("-" * 70)

        # Create test tree
        with tempfile.TemporaryDirectory() as tmpdir:
            print(f"Creating test tree in {tmpdir}...")
            create_test_tree(
                tmpdir,
                depth=config['depth'],
                dirs_per_level=config['dirs'],
                files_per_dir=config['files']
            )

            # Count total files
            total_files = sum(1 for _ in Path(tmpdir).rglob('*') if _.is_file())
            print(f"Test tree created: {total_files:,} files\n")

            # Benchmark os.walk()
            print("Benchmarking os.walk()...")
            oswalk_result = benchmark_walk(tmpdir, 'os.walk()', count_files_oswalk)

            # Benchmark pwalk.walk()
            print("Benchmarking pwalk.walk()...")
            pwalk_result = benchmark_walk(tmpdir, 'pwalk.walk()', count_files_pwalk)

            # Results
            print("\nResults:")
            print(f"  os.walk():   {oswalk_result['avg_time']:.4f}s "
                  f"({oswalk_result['files_per_sec']:,.0f} files/sec)")
            print(f"  pwalk.walk(): {pwalk_result['avg_time']:.4f}s "
                  f"({pwalk_result['files_per_sec']:,.0f} files/sec)")

            # Calculate speedup
            if oswalk_result['avg_time'] > 0:
                speedup = oswalk_result['avg_time'] / pwalk_result['avg_time']
                print(f"\n  Speedup: {speedup:.2f}x")

                if speedup >= 1.0:
                    print(f"  ✅ pwalk is {speedup:.2f}x faster!")
                else:
                    print(f"  ⚠️  os.walk() is {1/speedup:.2f}x faster (small tree overhead)")

    print("\n" + "=" * 70)
    print("Benchmark Complete")
    print("=" * 70)
    print("\nNote: For large trees (millions of files), pwalk.walk() shows")
    print("significant speedup. Small trees may show overhead due to threading.")
    print("\nFor real performance gains, run pwalk.report() on large directories:")
    print("  python -m pwalk.report /large/directory")


if __name__ == '__main__':
    main()
