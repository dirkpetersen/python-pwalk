# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

python-pwalk is a Python package that provides a parallel, thread-safe replacement for Python's `os.walk()` function. It is based on John Dey's pwalk C implementation from filesystem-reporting-tools, which uses multi-threaded directory traversal for POSIX file systems.

## Core Architecture

### High-Performance Data Flow

**Design Goal**: Maximum performance for petabyte-scale filesystems with billions of files.

**Data Pipeline**:
1. **C Extension Module** (best performance) → Binary format output
2. **Binary Buffer** (C-level) → Temporary files when memory threshold exceeded
3. **Python Layer** → Yields walk() results OR generates reports
4. **Output Formats**:
   - **Parquet (default)**: Columnar, compressed, optimized for DuckDB/Polars ingestion
   - **CSV (optional)**: 100% compatible with John Dey's pwalk output format

**Key Performance Decisions**:
- Use C extension module (not ctypes/cffi) for zero-overhead Python integration
- Binary struct format for internal buffering (fastest, no serialization overhead)
- Parquet for output (10-100x smaller than CSV, preserves types, instant DB loading)
- Keep DuckDB decoupled - users ingest Parquet themselves for analysis

### Dependency on pwalk C Implementation

The project depends on John Dey's pwalk tool (symlinked as `filesystem-reporting-tools/`). This tool will be modified for binary output and bundled with the package:

- Uses POSIX threads (pthreads) to walk file systems in parallel with configurable thread count
- Uses a thread pool architecture where threads recursively traverse directories
- Modified to output binary format (fastest) with CSV fallback for compatibility
- Supports traversal rates of 8,000-30,000 stats per second
- Key data structure: `struct threadData` (pwalk.h:8-18) contains path, parent inode, depth, thread ID, and stat information

### Thread Safety Model (from pwalk.c)

The C implementation uses two critical mutexes:
- `mutexFD`: Controls thread allocation/deallocation and thread slot management
- `mutexPrintStat`: Protects file processing output operations

Thread management logic (pwalk.c:214-237):
- When a directory is found, attempts to allocate a new thread from tdslot array
- If no threads available, performs recursive traversal in same thread
- Thread count tracked globally with ThreadCNT < MAXTHRDS (configurable in Python API)

## Python Package Requirements

### API Design

#### 1. walk() - os.walk() Compatible Iterator

The package must maintain 100% API compatibility with `os.walk()`:

```python
from pwalk import walk

for dirpath, dirnames, filenames in walk(
    top,
    topdown=True,
    onerror=None,
    followlinks=False,
    max_threads=None,  # Extension: defaults to cpu_count() or SLURM_CPUS_ON_NODE
    ignore_snapshots=True  # Extension: skip .snapshot dirs by default
):
    # Identical API to os.walk()
```

Key signature requirements (python.walk.md:1-12):
- `top`: Starting directory path
- `topdown`: If True, yield parent before children (allows in-place modification of dirnames)
  - **Note**: `topdown=False` collects all results in memory then reverses (inefficient for large trees)
- `onerror`: Callback function for OSError instances
- `followlinks`: Whether to follow symbolic links (default False to avoid infinite recursion)

**Thread Count Configuration**:
```python
# Default behavior:
max_threads = int(os.environ.get('SLURM_CPUS_ON_NODE', os.cpu_count()))
```

#### 2. report() - Metadata Collection with Error Tracking

Separate function for collecting full filesystem metadata:

```python
from pwalk import report

metadata, errors = report(
    top,
    format='parquet',  # or 'csv' for legacy compatibility
    output='filesystem_scan.parquet',
    max_threads=None,
    buffer_threshold='2GB'  # When to spill to temp binary files
)

# metadata: Generator or file path
# errors: List of full paths where access was denied
```

**CSV Format Compatibility**: When `format='csv'`, output is 100% compatible with John Dey's pwalk format (filesystem-reporting-tools/README.md:113-116).

#### 3. repair() - Filesystem Repair Operations

For permission repair when running as root:

```python
from pwalk import repair

repair(
    top,
    dry_run=True,
    change_gids=[1234, 5678],
    force_group_writable=True,
    exclude=['/path/to/skip']
)
```

### Thread Safety Requirements

The implementation is thread-safe because:
1. C extension uses mutexes for thread coordination (mutexFD, mutexPrintStat)
2. Binary output is written to thread-local buffers before merging
3. Python GIL protection when crossing C/Python boundary

### Snapshot Directory Handling

CRITICAL: `.snapshot` directories (NetApp/enterprise storage) are **skipped by default** via `ignore_snapshots=True`. Users must explicitly set to `False` to traverse snapshots.

### Large-Scale Data Management

For petabyte filesystems with billions of files:

1. **Binary Buffering (C-level)**: When memory threshold exceeded, write binary structs to temp files
2. **No Python Serialization**: All buffering happens in C for maximum performance
3. **Streaming Output**: For walk(), yield results incrementally (no memory limits)
4. **Compressed Output**: Parquet default reduces 100GB CSV to ~1-10GB
5. **Hard Link Identification**: Metadata includes inode info to identify hard links (not skipped, just flagged)

## Development Commands

### Building C Extension Module

```bash
# Development build (in-place)
python setup.py build_ext --inplace

# Install for testing
pip install -e .

# Build wheels for distribution
python -m build
```

### Building Original pwalk (for reference)

```bash
cd filesystem-reporting-tools
gcc -pthread pwalk.c exclude.c fileProcess.c -o pwalk
./pwalk --NoSnap /path/to/test/directory
```

### Testing

```bash
# Unit tests
pytest tests/

# Performance benchmark
python benchmarks/compare_os_walk.py

# Integration test
python -m pwalk.cli --format=parquet --output=/tmp/test.parquet /path/to/test
```

## C Extension Implementation Details

### 1. Binary Output Mode with Versioning

Fork pwalk.c and add binary struct output with versioning:

```c
#define PWALK_BINARY_VERSION 1
#define PWALK_MAGIC 0x5057414C  // 'PWAL'

// Binary file header for versioning and compatibility
struct BinaryHeader {
    uint32_t magic;
    uint16_t version;
    uint16_t flags;  // endianness, platform info
    uint64_t record_count;  // updated at end
    uint64_t timestamp;
} __attribute__((packed));

// Binary struct for file metadata (packed for efficiency)
struct FileRecord {
    ino_t inode;
    ino_t parent_inode;
    int32_t depth;
    uid_t uid;
    gid_t gid;
    off_t size;
    dev_t st_dev;
    blkcnt_t st_blocks;
    nlink_t st_nlink;
    mode_t st_mode;
    time_t atime;
    time_t mtime;
    time_t ctime;
    int64_t file_count;  // -1 if not directory
    int64_t dir_sum;     // sum of file sizes in directory
    uint16_t filename_len;
    uint16_t extension_len;
    char data[];  // filename + extension (null-terminated)
} __attribute__((packed));
```

### 2. Memory Management (C-level)

Track allocated memory precisely:

```c
// Memory tracking structure
typedef struct {
    size_t allocated;      // Current allocated memory
    size_t threshold;      // Max before spilling to disk
    int temp_fd;          // Temporary file descriptor
    char temp_path[PATH_MAX];
    pthread_mutex_t mem_mutex;
} MemoryTracker;

// Track each allocation
void* tracked_malloc(MemoryTracker *tracker, size_t size) {
    pthread_mutex_lock(&tracker->mem_mutex);
    if (tracker->allocated + size > tracker->threshold) {
        flush_to_temp_file(tracker);
    }
    tracker->allocated += size;
    pthread_mutex_unlock(&tracker->mem_mutex);
    return malloc(size);
}
```

### 3. Python/C Boundary - Queue Pattern (Fastest)

Use producer/consumer queue for maximum performance:

```c
// High-performance ring buffer queue
typedef struct {
    void **items;
    size_t capacity;
    size_t head;
    size_t tail;
    pthread_mutex_t mutex;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    int done;  // Signal traversal complete
} RingBuffer;

// C threads produce directory entries
void producer_thread(RingBuffer *queue, char *path);

// Python consumes via C extension
PyObject* pwalk_next(PyObject *self) {
    DirEntry *entry = ringbuffer_get(queue);
    if (!entry) Py_RETURN_NONE;
    return build_python_tuple(entry);
}
```

### 4. Error Handling Strategy

Continue on all errors with reporting:

```c
void handle_traversal_error(const char *path, int error_code) {
    // Print to stderr (thread-safe)
    pthread_mutex_lock(&error_mutex);
    fprintf(stderr, "ERROR: %s: %s (errno=%d)\n",
            path, strerror(error_code), error_code);

    // Add to error list for report()
    if (error_list) {
        add_error_to_list(error_list, path, error_code);
    }
    pthread_mutex_unlock(&error_mutex);

    // Continue traversal - never abort
}
```

### 5. Parquet Writing - Direct Streaming (Fastest)

Stream directly to PyArrow during traversal:

```python
# In C extension wrapper
class ParquetStreamWriter:
    def __init__(self, path, schema):
        self.writer = pq.ParquetWriter(path, schema, compression='snappy')
        self.batch_size = 10000
        self.batch = []

    def write_record(self, record):
        self.batch.append(record)
        if len(self.batch) >= self.batch_size:
            self.flush()

    def flush(self):
        if self.batch:
            table = pa.Table.from_pydict(self._batch_to_dict())
            self.writer.write_table(table)
            self.batch = []
```

### 6. Progress Reporting Implementation

Add comprehensive progress tracking:

```c
typedef struct {
    uint64_t files_processed;
    uint64_t dirs_processed;
    uint64_t bytes_processed;
    time_t start_time;
    time_t last_report_time;
    void (*callback)(ProgressInfo*);  // Python callback
} ProgressInfo;

// Signal handling for graceful interruption
volatile sig_atomic_t interrupted = 0;
void sigint_handler(int sig) {
    interrupted = 1;
    // Dump intermediate results
    flush_all_buffers();
    write_checkpoint_file();
}

// Checkpoint for resume
typedef struct {
    char last_path[PATH_MAX];
    uint64_t files_processed;
    struct BinaryHeader header;
} Checkpoint;
```

### 7. Security Implementation for repair()

```c
// Validate against /etc/group
int validate_gid(gid_t gid) {
    struct group *grp = getgrgid(gid);
    if (!grp) {
        fprintf(stderr, "WARNING: GID %d not in /etc/group\n", gid);
        return 0;
    }
    return 1;
}

// Safeguards for system directories
const char *protected_paths[] = {
    "/", "/bin", "/boot", "/dev", "/etc", "/lib",
    "/lib64", "/proc", "/root", "/sbin", "/sys", "/usr",
    NULL
};

int is_protected_path(const char *path) {
    for (int i = 0; protected_paths[i]; i++) {
        if (strncmp(path, protected_paths[i], strlen(protected_paths[i])) == 0) {
            syslog(LOG_WARNING, "pwalk: Attempted modification of protected path: %s", path);
            return 1;
        }
    }
    return 0;
}

// Audit logging
void log_change(const char *path, uid_t old_uid, gid_t old_gid,
                uid_t new_uid, gid_t new_gid) {
    syslog(LOG_INFO, "pwalk repair: %s: uid %d->%d, gid %d->%d",
           path, old_uid, new_uid, old_gid, new_gid);
}
```

## Output Format Specifications

### Parquet Schema (Default)

```
inode: int64
parent_inode: int64
depth: int32
filename: string
extension: string (nullable)
uid: int32
gid: int32
size: int64
st_dev: int64
st_blocks: int64
st_nlink: int32
st_mode: int32
atime: timestamp
mtime: timestamp
ctime: timestamp
file_count: int64 (-1 if not directory)
dir_sum: int64 (sum of sizes in directory)
is_hardlink: bool
```

### CSV Schema (Legacy Compatible)

Must match John Dey's pwalk output format exactly (filesystem-reporting-tools/README.md:113-116):
```
inode,parent-inode,directory-depth,"filename","fileExtension",UID,GID,st_size,st_dev,st_blocks,st_nlink,"st_mode",st_atime,st_mtime,st_ctime,pw_fcount,pw_dirsum
```

## Key Implementation Considerations

### Exclude Lists

Support excluding directories from traversal (pwalk.c:212-213):
- File containing one path per line to exclude
- Paths should match the format used by pwalk output

### Cross-Filesystem Boundaries

Support `--one-file-system` / `-x` flag (pwalk.c:58, 203-204):
- Track st_dev of starting directory
- Skip directories on different filesystems

### Performance Expectations

Target performance metrics based on pwalk C implementation (filesystem-reporting-tools/README.md:109-121):
- 8,000-30,000 stat operations per second
- Example: 50M files should complete in ~41 minutes at 20K stats/sec
- Performance depends on: storage system speed, host system, file layout
- Small directories benefit less from parallelization (thread overhead)

## DuckDB Integration Pattern

While DuckDB is **not** a dependency, the Parquet output is optimized for DuckDB ingestion:

```python
# 1. Generate report
from pwalk import report
metadata, errors = report('/data', format='parquet', output='scan.parquet')

# 2. Load into DuckDB (user's code)
import duckdb
con = duckdb.connect('filesystem.db')
con.execute("CREATE TABLE fs AS SELECT * FROM 'scan.parquet'")

# 3. Analyze filesystem
result = con.execute("""
    SELECT uid, count(*), sum(size)
    FROM fs
    WHERE ctime > unix_timestamp(now() - INTERVAL 7 DAY)
    GROUP BY uid
    ORDER BY sum(size) DESC
""").fetchall()
```

**Performance**: Parquet's columnar compression automatically deduplicates repeated values (uid/gid/mode), typically achieving 10-100x compression vs CSV.

## Implementation Roadmap

### Phase 1: C Extension Foundation
1. Fork pwalk.c and create pwalk_binary.c with binary output support
2. Add memory tracking and buffer management
3. Implement ring buffer queue for Python/C communication
4. Add progress reporting callbacks and signal handling

### Phase 2: Python Package Structure
1. Create setup.py with C extension compilation
2. Implement pwalk/__init__.py with walk() function
3. Create pwalk/report.py for metadata collection
4. Implement pwalk/repair.py for filesystem repairs
5. Add pwalk/cli.py for command-line interface

### Phase 3: Performance Optimization
1. Implement direct PyArrow streaming for Parquet output
2. Optimize ring buffer size and batching
3. Profile and tune thread count defaults
4. Add checkpoint/resume capability

### Phase 4: Production Features
1. Add /etc/group validation for repair operations
2. Implement syslog audit trail
3. Add protected path safeguards
4. Create comprehensive error reporting

### Phase 5: Distribution
1. Build manylinux wheels for PyPI
2. Create conda-forge recipe
3. Add GitHub Actions CI/CD
4. Write comprehensive documentation

## GitHub Actions CI/CD Pipeline

### Workflow Files Structure

#### `.github/workflows/test.yml` - Continuous Integration
```yaml
name: Test

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ['3.8', '3.9', '3.10', '3.11', '3.12']

    steps:
    - uses: actions/checkout@v3
      with:
        submodules: recursive

    - name: Set up Python ${{ matrix.python-version }}
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}

    - name: Install build dependencies
      run: |
        python -m pip install --upgrade pip
        pip install setuptools wheel pytest pytest-cov pyarrow

    - name: Build C extension
      run: |
        python setup.py build_ext --inplace

    - name: Run tests
      run: |
        pytest tests/ -v --cov=pwalk --cov-report=xml

    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

#### `.github/workflows/build-wheels.yml` - Build Distribution
```yaml
name: Build Wheels

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

jobs:
  build_wheels:
    name: Build wheels on ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-20.04, macos-11, macos-12]

    steps:
    - uses: actions/checkout@v3
      with:
        submodules: recursive

    - name: Build wheels
      uses: pypa/cibuildwheel@v2.16.2
      env:
        CIBW_BUILD: cp38-* cp39-* cp310-* cp311-* cp312-*
        CIBW_SKIP: "*-musllinux_* *-win32"
        CIBW_MANYLINUX_X86_64_IMAGE: manylinux2014
        CIBW_BEFORE_BUILD: pip install setuptools wheel
        CIBW_TEST_REQUIRES: pytest pyarrow
        CIBW_TEST_COMMAND: "pytest {project}/tests"

    - uses: actions/upload-artifact@v3
      with:
        name: wheels
        path: ./wheelhouse/*.whl
```

#### `.github/workflows/publish-pypi.yml` - PyPI Release
```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  build:
    uses: ./.github/workflows/build-wheels.yml

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/pwalk
    permissions:
      id-token: write  # OIDC publishing

    steps:
    - uses: actions/download-artifact@v3
      with:
        name: wheels
        path: dist/

    - name: Publish to PyPI
      uses: pypa/gh-action-pypi-publish@release/v1
      # Uses OIDC trusted publishing - no API tokens needed
```

#### `.github/workflows/benchmark.yml` - Performance Testing
```yaml
name: Benchmark

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday
  workflow_dispatch:

jobs:
  benchmark:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3
    - name: Setup Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        pip install -e .
        pip install pytest-benchmark

    - name: Run benchmarks
      run: |
        pytest benchmarks/ --benchmark-only --benchmark-json=output.json

    - name: Store benchmark result
      uses: benchmark-action/github-action-benchmark@v1
      with:
        tool: 'pytest'
        output-file-path: output.json
        github-token: ${{ secrets.GITHUB_TOKEN }}
        auto-push: true
```

## Comprehensive Test Strategy

### Test Structure

```
tests/
├── unit/
│   ├── test_binary_format.py      # Binary struct serialization
│   ├── test_memory_tracker.py     # Memory threshold management
│   ├── test_ring_buffer.py        # Queue implementation
│   └── test_error_handling.py     # Error collection
├── integration/
│   ├── test_walk_compatibility.py # os.walk() API compatibility
│   ├── test_snapshot_exclusion.py # .snapshot directory handling
│   ├── test_thread_scaling.py     # Thread count configuration
│   └── test_slurm_integration.py  # SLURM_CPUS_ON_NODE
├── system/
│   ├── test_large_filesystem.py   # Mock filesystem with millions of files
│   ├── test_parquet_output.py     # Parquet generation and schema
│   ├── test_csv_compatibility.py  # CSV format matching pwalk
│   └── test_checkpoint_resume.py  # Interruption and resume
├── security/
│   ├── test_repair_safeguards.py  # Protected path validation
│   ├── test_gid_validation.py     # /etc/group checking
│   └── test_audit_logging.py      # Syslog integration
└── benchmarks/
    ├── bench_vs_oswalk.py          # Speed comparison
    ├── bench_memory_usage.py       # Memory profiling
    └── bench_thread_scaling.py     # Optimal thread counts
```

### Key Test Cases

#### 1. Binary Format Tests
```python
# test_binary_format.py
def test_binary_header_versioning():
    """Verify binary header contains correct magic and version"""
    header = create_binary_header()
    assert header.magic == 0x5057414C  # 'PWAL'
    assert header.version == 1

def test_binary_record_packing():
    """Ensure struct packing is consistent across platforms"""
    record = FileRecord(inode=12345, uid=1000, gid=1000)
    packed = pack_record(record)
    assert len(packed) == EXPECTED_RECORD_SIZE

def test_endianness_handling():
    """Verify endianness flags in binary header"""
    # Test both big and little endian
```

#### 2. Memory Management Tests
```python
# test_memory_tracker.py
def test_memory_threshold_spill():
    """Test spilling to disk when threshold exceeded"""
    tracker = MemoryTracker(threshold=1024*1024)  # 1MB
    for i in range(10000):
        tracker.add_record(create_large_record())
    assert tracker.temp_files_created > 0

def test_concurrent_memory_tracking():
    """Verify thread-safe memory tracking"""
    # Spawn multiple threads allocating memory
    # Verify no race conditions
```

#### 3. API Compatibility Tests
```python
# test_walk_compatibility.py
@pytest.mark.parametrize("topdown", [True, False])
def test_walk_api_compatibility(tmp_path, topdown):
    """Ensure pwalk.walk matches os.walk exactly"""
    create_test_tree(tmp_path)

    os_results = list(os.walk(tmp_path, topdown=topdown))
    pw_results = list(pwalk.walk(tmp_path, topdown=topdown))

    assert os_results == pw_results

def test_dirnames_modification(tmp_path):
    """Test in-place modification of dirnames to prune traversal"""
    for dirpath, dirnames, filenames in pwalk.walk(tmp_path):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        # Verify hidden directories are skipped
```

#### 4. Large Filesystem Simulation
```python
# test_large_filesystem.py
class MockFilesystem:
    """Generate virtual filesystem for testing at scale"""
    def __init__(self, total_files=1_000_000):
        self.total_files = total_files

    def generate_tree(self):
        """Create balanced tree with specified file count"""
        # Generate without actually creating files

@pytest.mark.slow
def test_billion_files_performance():
    """Test with simulated billion-file filesystem"""
    fs = MockFilesystem(1_000_000_000)
    start = time.time()
    count = sum(1 for _ in pwalk.walk_mock(fs))
    elapsed = time.time() - start
    assert elapsed < 3600  # Under 1 hour
```

#### 5. Error Handling Tests
```python
# test_error_handling.py
def test_permission_denied_collection():
    """Verify errors are collected but traversal continues"""
    # Create directories with no read permission
    errors = []
    for _ in pwalk.walk('/restricted', onerror=errors.append):
        pass
    assert len(errors) > 0
    assert all(isinstance(e, OSError) for e in errors)

def test_stale_nfs_handling():
    """Test handling of stale NFS handles"""
    # Mock stale NFS scenario
```

#### 6. Security Tests
```python
# test_repair_safeguards.py
@pytest.mark.require_root
def test_protected_path_rejection():
    """Ensure system directories cannot be modified"""
    with pytest.raises(ProtectedPathError):
        pwalk.repair('/etc', dry_run=False)

def test_gid_validation():
    """Verify GIDs are validated against /etc/group"""
    invalid_gid = 99999
    result = pwalk.validate_gid(invalid_gid)
    assert result is False
```

#### 7. Benchmark Tests
```python
# bench_vs_oswalk.py
def test_pwalk_faster_than_oswalk(benchmark, large_tree):
    """Benchmark pwalk vs os.walk"""
    def run_pwalk():
        list(pwalk.walk(large_tree))

    result = benchmark(run_pwalk)
    # Compare with os.walk baseline
```

### Test Data Generation

```python
# tests/fixtures.py
@pytest.fixture
def filesystem_tree(tmp_path):
    """Generate test filesystem tree"""
    structure = {
        'depth': 5,
        'dirs_per_level': 10,
        'files_per_dir': 100,
        'include_symlinks': True,
        'include_hardlinks': True,
        'include_snapshots': True
    }
    return generate_tree(tmp_path, **structure)

@pytest.fixture
def slurm_environment(monkeypatch):
    """Mock SLURM environment variables"""
    monkeypatch.setenv('SLURM_CPUS_ON_NODE', '16')
    return 16
```

### Performance Assertions

```python
# tests/performance.py
def test_stat_operations_per_second():
    """Verify performance meets targets"""
    stats_per_sec = measure_stat_performance()
    assert 8000 <= stats_per_sec <= 30000

def test_memory_usage_under_threshold():
    """Ensure memory stays within configured limits"""
    # Monitor RSS during large traversal
    assert max_memory_used < configured_threshold
```

## Build Requirements

The package requires GCC and pthread libraries to compile:

```bash
# Ubuntu/Debian
sudo apt-get install build-essential

# RHEL/CentOS/Rocky
sudo yum install gcc gcc-c++ make

# macOS
xcode-select --install
```

Python dependencies:
- pyarrow (for Parquet support)
- setuptools (for building)
- pytest (for testing, optional)

## Project Status

Currently in initial development phase:
- No Python code exists yet
- No setup.py, pyproject.toml, or package structure defined
- C extension modifications to pwalk.c not yet implemented
- Core architecture and API design finalized (this document)
- Implementation roadmap defined with 5 phases
