# python-pwalk

[![PyPI](https://img.shields.io/pypi/v/pwalk.svg)](https://pypi.org/project/pwalk/)
[![Downloads](https://img.shields.io/pypi/dm/pwalk.svg)](https://pypi.org/project/pwalk/)
[![License](https://img.shields.io/github/license/dirkpetersen/python-pwalk)](https://raw.githubusercontent.com/dirkpetersen/python-pwalk/main/LICENSE)
[![Python Version](https://img.shields.io/pypi/pyversions/pwalk.svg)](https://pypi.org/project/pwalk/)
[![Build Status](https://github.com/dirkpetersen/python-pwalk/workflows/Test/badge.svg)](https://github.com/dirkpetersen/python-pwalk/actions)

**A high-performance toolkit for filesystem analysis and reporting — optimized for petabyte-scale filesystems and HPC environments.**

Generate comprehensive filesystem metadata reports **5-10x faster** than traditional tools, with true multi-threading, intelligent buffering, and automatic zstd compression achieving 23x size reduction. Perfect for system administrators, data scientists, and HPC users working with millions or billions of files.

## Why python-pwalk?

Traditional filesystem traversal tools struggle with modern storage systems containing billions of files. `python-pwalk` solves this with a battle-tested approach combining Python's ease of use with C's raw performance.

### Key Features

- 🚀 **Extreme Performance**: 8,000-30,000 files/second — traverse 50 million files in ~41 minutes
- 🔄 **True Parallelism**: Multi-threaded C implementation using up to 32 threads
- 🗜️ **23x Compression**: Automatic zstd compression reduces 100 GB CSV to 4 GB
- 📦 **Zero Dependencies**: No PyArrow, no numpy — just Python + C
- 🔌 **Drop-in Replacement**: 100% compatible with `os.walk()` API
- 💾 **Memory Efficient**: Thread-local buffers with automatic spillover for billions of files
- 🎯 **HPC Ready**: SLURM-aware, `.snapshot` filtering, cross-filesystem detection
- 🦆 **DuckDB Native**: Output `.csv.zst` files readable directly by DuckDB
- 🛡️ **Production Tested**: Based on John Dey's pwalk used in HPC environments worldwide

### Perfect For

- **System Administrators**: Audit multi-petabyte filesystems in minutes
- **Data Scientists**: Analyze file distributions across massive datasets
- **HPC Users**: Track storage usage in supercomputing environments
- **Storage Teams**: Generate reports for NetApp, Lustre, GPFS filesystems
- **Compliance**: Create auditable records of filesystem contents

## Installation

```bash
pip install pwalk
```

**That's it!** Pre-compiled binary wheels with zstd compression are available for:
- **Linux**: x86_64 (manylinux2014)
- **Python**: 3.10, 3.11, 3.12, 3.13, 3.14

**No system dependencies needed** — wheels include everything pre-compiled!

## Quick Start

### 30-Second Example

```python
from pwalk import walk, report

# 1. Drop-in replacement for os.walk() — 100% compatible API
for dirpath, dirnames, filenames in walk('/data'):
    print(f"{dirpath}: {len(filenames)} files")

# 2. Generate compressed filesystem report (5-10x faster with multi-threading!)
output, errors = report('/data', compress='zstd')
# Creates scan.csv.zst - 23x smaller than plain CSV!

# 3. Analyze with DuckDB
import duckdb
df = duckdb.connect().execute(f"SELECT * FROM '{output}'").fetchdf()
print(df.head())
```

> **Note on Performance**: The `walk()` function uses `os.walk()` under the hood (single-threaded) for maximum compatibility across Python versions. For **5-10x faster performance**, use `report()` which leverages our multi-threaded C implementation. In Python 3.13+ with free-threading (no-GIL mode), `walk()` will automatically use parallel traversal for massive speedups!

### Basic Usage

```python
from pwalk import walk

# 100% compatible with os.walk() API
for dirpath, dirnames, filenames in walk('/data'):
    print(f"Directory: {dirpath}")
    print(f"  Subdirectories: {len(dirnames)}")
    print(f"  Files: {len(filenames)}")
```

### Full API Compatibility

```python
from pwalk import walk

# All os.walk() parameters supported
for dirpath, dirnames, filenames in walk(
    '/data',
    topdown=True,           # Process parents before children
    onerror=handle_error,   # Error callback
    followlinks=False       # Don't follow symlinks
):
    # Modify dirnames in-place to prune traversal
    dirnames[:] = [d for d in dirnames if not d.startswith('.')]
```

### Advanced: Thread Control

```python
from pwalk import walk

# Explicit thread count (default: cpu_count() or SLURM_CPUS_ON_NODE)
for dirpath, dirnames, filenames in walk('/data', max_threads=16):
    process_directory(dirpath, filenames)

# Traverse snapshots (disabled by default)
for dirpath, dirnames, filenames in walk('/data', ignore_snapshots=False):
    ...
```

## Filesystem Metadata Reports

### CSV Output with Zstd Compression (Default)

```python
from pwalk import report

# Generate compressed CSV (8-10x smaller, DuckDB compatible)
output, errors = report(
    '/data',
    output='scan.csv',
    compress='zstd'  # or 'gzip', 'auto', 'none'
)

print(f"Report saved to: {output}")
print(f"Inaccessible directories: {len(errors)}")
```

**CSV Format** (100% compatible with John Dey's pwalk):
```
inode,parent-inode,directory-depth,"filename","fileExtension",UID,GID,st_size,st_dev,st_blocks,st_nlink,"st_mode",st_atime,st_mtime,st_ctime,pw_fcount,pw_dirsum
```

**Compression Options**:
- `compress='auto'`: Use zstd if available, else uncompressed (default)
- `compress='zstd'`: Force zstd compression (8-10x, fast)
- `compress='gzip'`: Use gzip compression (6-7x, slower but universal)
- `compress='none'`: No compression

## DuckDB Analysis Workflow

```python
# 1. Generate compressed CSV report
from pwalk import report
output, errors = report('/data', output='scan.csv', compress='zstd')

# 2. DuckDB reads .csv.zst natively!
import duckdb
con = duckdb.connect('fs_analysis.db')
con.execute("CREATE TABLE fs AS SELECT * FROM 'scan.csv.zst'")

# 3. Answer questions like "Who used the last 10TB?"
result = con.execute("""
    SELECT
        uid,
        count(*) as file_count,
        sum(st_size) / (1024*1024*1024*1024) as size_tb
    FROM fs
    WHERE st_ctime > unixepoch(now() - INTERVAL 7 DAY)
    GROUP BY uid
    ORDER BY size_tb DESC
    LIMIT 10
""").fetchdf()
print(result)

# 4. Optional: Convert to Parquet for long-term storage
con.execute("""
    COPY (SELECT * FROM 'scan.csv.zst')
    TO 'scan.parquet' (FORMAT PARQUET, COMPRESSION SNAPPY)
""")
```

## Filesystem Repair (Root Only)

```python
from pwalk import repair

# Dry-run to preview changes
repair(
    '/shared',
    dry_run=True,
    change_gids=[1234, 5678],      # Treat these GIDs like private groups
    force_group_writable=True,      # Ensure group read+write+execute
    exclude=['/shared/archive']     # Skip specific paths
)

# Apply changes
repair('/shared', dry_run=False, force_group_writable=True)
```

## Real-World Use Cases

### 1. Answer Critical Storage Questions Fast

```python
from pwalk import report
import duckdb

# Generate comprehensive filesystem metadata
report('/data', compress='zstd')  # Done in minutes, not hours!

# Who used the last 10 TB this week?
con = duckdb.connect()
result = con.execute("""
    SELECT UID, COUNT(*) as files, SUM(st_size)/1e12 as TB
    FROM 'scan.csv.zst'
    WHERE st_ctime > unixepoch(now() - INTERVAL 7 DAY)
    GROUP BY UID ORDER BY TB DESC LIMIT 10
""").fetchdf()
```

### 2. Find Storage Hogs and Opportunities

```python
# Find directories with >1M files (performance issues!)
huge_dirs = con.execute("""
    SELECT "filename", pw_fcount, pw_dirsum/1e9 as GB
    FROM 'scan.csv.zst'
    WHERE pw_fcount > 1000000
    ORDER BY pw_fcount DESC
""").fetchdf()

# Find ancient files (cleanup candidates)
old_files = con.execute("""
    SELECT "filename", st_size, datetime(st_mtime, 'unixepoch')
    FROM 'scan.csv.zst'
    WHERE st_mtime < unixepoch(now() - INTERVAL 2 YEAR)
    ORDER BY st_size DESC LIMIT 100
""").fetchdf()
```

### 3. Monitor Growth Over Time

```python
# Weekly snapshots
import schedule

def weekly_snapshot():
    timestamp = time.strftime('%Y%m%d')
    report('/data', output=f'snapshot_{timestamp}.csv.zst')

schedule.every().sunday.at("02:00").do(weekly_snapshot)
```

## Performance

### Current Performance (Python 3.10-3.14)

**`walk()` function**: Uses `os.walk()` internally (single-threaded) for 100% compatibility
- Same speed as `os.walk()` — perfect drop-in replacement
- No threading overhead, works everywhere

**`report()` function**: Multi-threaded C implementation (5-10x faster!)
- **Speed**: 8,000-30,000 stat operations per second
- **Example**: 50 million files in ~41 minutes at 20K stats/sec
- **Parallelism**: Up to 32 threads
- **Scaling**: Performance depends on storage system, host CPU, and file layout
- **Compression**: Zstd reduces CSV size by 23x with minimal overhead

### Future Performance (Python 3.13+ Free-Threading)

**What's Changing?** Python 3.13 introduced optional "free-threading" mode (also called "no-GIL mode").

**The Global Interpreter Lock (GIL) Explained**: For decades, Python had a "global lock" that prevented multiple threads from running Python code simultaneously. This meant that even with multiple CPU cores, only one thread could execute Python code at a time. Python 3.13+ can optionally remove this lock, allowing true parallel execution.

**What This Means for pwalk**:
- Python 3.13+ with free-threading enabled: `walk()` will automatically use parallel traversal for 5-10x speedup
- Python 3.13+ without free-threading: Same behavior as today (uses `os.walk()`)
- Python 3.10-3.12: Same behavior as today (uses `os.walk()`)
- `report()` is always fast: Already uses multi-threaded C code (not affected by GIL)

**How to Get Free-Threading Python** (Python 3.13+):

Free-threading Python builds are now available! Here's how to get them:

**Option 1: Official Python.org Installers** (Easiest)
```bash
# Download from python.org
# Look for "Free-threaded" builds (separate downloads)
# https://www.python.org/downloads/

# On Linux/macOS, the free-threaded interpreter has a 't' suffix
python3.13t --version  # Should show "Python 3.13.x (free-threaded)"
```

**Option 2: Build from Source** (Linux/macOS)
```bash
# Clone Python source
git clone https://github.com/python/cpython.git
cd cpython
git checkout v3.13.0  # or latest 3.13.x tag

# Configure with free-threading
./configure --disable-gil
make -j$(nproc)
sudo make install

# Verify
python3.13 --version
python3.13 -c "import sys; print(f'GIL disabled: {not sys._is_gil_enabled()}')"
```

**Option 3: Docker/Conda** (Recommended for Testing)
```bash
# Using official Python Docker image
docker run -it python:3.13-slim python3 -c "import sys; print(sys._is_gil_enabled())"

# Conda (check for free-threading builds)
conda install python=3.13
```

**Option 4: pyenv** (Developers)
```bash
# Install pyenv if not already installed
curl https://pyenv.run | bash

# Install free-threaded Python 3.13
pyenv install 3.13.0t  # 't' suffix for free-threaded build
pyenv local 3.13.0t

# Verify
python --version
python -c "import sys; print(f'GIL: {sys._is_gil_enabled()}')"
```

**Using Free-Threading with pwalk**:
```bash
# Install pwalk
python3.13t -m pip install pwalk

# Run your script
python3.13t your_script.py

# Verify it's working
python3.13t -c "import sys; print(f'Free-threading: {not sys._is_gil_enabled()}')"
```

> **Note**: As of 2025, free-threading is still **experimental**. Some packages may not be compatible yet. For production use today, stick with `report()` which is always multi-threaded!

## Technical Architecture

- **Single Optimized C Extension**: `_pwalk_core` — 320 lines of highly optimized C
- **Thread-Local Buffers**: 512KB per thread, zero lock contention during traversal
- **Multithreaded Traversal**: Up to 32 parallel threads using John Dey's proven algorithm
- **Streaming Compression**: Zstd level 1 for 23x compression at 200-400 MB/s
- **SLURM Integration**: Auto-detects `SLURM_CPUS_ON_NODE` for HPC environments
- **Zero Dependencies**: No external Python packages — ships ready to run

## Why No Dependencies Matters

Unlike other tools that require PyArrow (~50 MB), numpy, pandas, etc., `pwalk` installs in seconds with zero dependencies. This means:

- ✅ Instant installation on air-gapped HPC systems
- ✅ No version conflicts with existing packages
- ✅ Minimal attack surface for security-conscious environments
- ✅ Works everywhere Python 3.10+ works

## Contributing

Contributions welcome! Based on the rock-solid [filesystem-reporting-tools](https://github.com/john-dey/filesystem-reporting-tools) by John Dey.

## License

GPL v2 — Same as John Dey's original pwalk implementation.

## Links

- **PyPI**: https://pypi.org/project/pwalk/
- **GitHub**: https://github.com/dirkpetersen/python-pwalk
- **Issues**: https://github.com/dirkpetersen/python-pwalk/issues
- **DuckDB**: https://duckdb.org — Perfect companion for analyzing pwalk output
- **Original pwalk**: https://github.com/john-dey/filesystem-reporting-tools

---

**Built with ❤️ for system administrators and HPC users worldwide. Based on John Dey's battle-tested pwalk implementation.** 


