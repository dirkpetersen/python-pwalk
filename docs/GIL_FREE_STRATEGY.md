# GIL-Free Performance Strategy for Network Filesystems

## Executive Summary

This document outlines advanced performance optimizations for python-pwalk targeting network filesystems (NFS, Weka, Lustre, BeeGFS, GPFS) on modern Linux kernels (5.0+). The strategy focuses on GIL-free operations, CPU affinity, kernel-level optimizations, and network-aware traversal patterns.

## Core Performance Principles

### 1. GIL-Free Architecture

**Design Goal**: Minimize Python GIL contention by keeping all performance-critical operations in C.

```c
// GIL-free thread worker structure
typedef struct {
    pthread_t thread;
    int cpu_id;                    // CPU affinity
    struct ring_buffer *work_queue; // Lock-free queue
    struct ring_buffer *result_queue;
    atomic_uint64_t stats_processed;
    atomic_uint64_t bytes_scanned;
    int numa_node;                  // NUMA awareness
} WorkerThread;

// Release GIL during traversal
static PyObject* pwalk_traverse_nogil(PyObject *self, PyObject *args) {
    Py_BEGIN_ALLOW_THREADS
    // All traversal happens here without GIL
    perform_parallel_traversal(&config);
    Py_END_ALLOW_THREADS

    return results;
}
```

### 2. CPU Affinity and NUMA Optimization

**Strategy**: Pin threads to specific CPUs and respect NUMA boundaries for network filesystem access.

```c
#define _GNU_SOURCE
#include <sched.h>
#include <numa.h>

// CPU affinity configuration
typedef struct {
    int enable_cpu_affinity;
    int enable_numa_binding;
    int cpus_per_thread;
    int prefer_local_numa;
    cpu_set_t cpu_mask;
} AffinityConfig;

// Set CPU affinity for worker thread
int set_thread_affinity(WorkerThread *worker, AffinityConfig *config) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);

    if (config->enable_cpu_affinity) {
        // Pin to specific CPU core
        CPU_SET(worker->cpu_id, &cpuset);

        // For SMT/hyperthreading, optionally use sibling cores
        if (config->cpus_per_thread > 1) {
            int sibling = worker->cpu_id + (sysconf(_SC_NPROCESSORS_ONLN) / 2);
            CPU_SET(sibling, &cpuset);
        }

        pthread_setaffinity_np(worker->thread, sizeof(cpu_set_t), &cpuset);
    }

    if (config->enable_numa_binding && numa_available() >= 0) {
        // Bind memory allocations to local NUMA node
        numa_set_localalloc();

        // Set preferred NUMA node for this thread
        numa_run_on_node(worker->numa_node);
    }

    return 0;
}

// Intelligent thread-to-CPU mapping
void optimize_thread_placement(WorkerThread *workers, int num_workers) {
    int num_cpus = sysconf(_SC_NPROCESSORS_ONLN);
    int num_numa_nodes = numa_max_node() + 1;

    // Distribute threads across NUMA nodes
    for (int i = 0; i < num_workers; i++) {
        workers[i].numa_node = i % num_numa_nodes;
        workers[i].cpu_id = distribute_across_numa(i, num_numa_nodes, num_cpus);
    }
}
```

### 3. Direct I/O and Kernel Optimizations

**Strategy**: Use O_DIRECT and other kernel features to bypass cache for network filesystems.

```c
#include <fcntl.h>
#include <sys/syscall.h>
#include <linux/fs.h>

// Advanced directory reading with getdents64
typedef struct {
    int use_getdents;      // Use getdents64 instead of readdir
    int use_o_direct;      // O_DIRECT for network FS
    int readahead_kb;      // Readahead size
    int use_statx;         // Use statx for selective stat
    int batch_size;        // Batch syscalls
} KernelOptConfig;

// High-performance directory reading using getdents64
int read_directory_optimized(const char *path, KernelOptConfig *config) {
    int fd = open(path, O_RDONLY | O_DIRECTORY |
                  (config->use_o_direct ? O_DIRECT : 0));

    if (fd < 0) return -1;

    // Set readahead for network FS
    if (config->readahead_kb > 0) {
        posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL);
        // For Linux 5.0+, use custom readahead
        syscall(SYS_readahead, fd, 0, config->readahead_kb * 1024);
    }

    // Use getdents64 for batch reading
    if (config->use_getdents) {
        char buffer[32768];  // 32KB buffer for entries
        while (1) {
            int nread = syscall(SYS_getdents64, fd, buffer, sizeof(buffer));
            if (nread <= 0) break;

            // Process entries in batch
            process_dirent_batch(buffer, nread);
        }
    }

    close(fd);
    return 0;
}

// Use statx for selective metadata retrieval
int stat_optimized(const char *path, struct stat *st, KernelOptConfig *config) {
    if (config->use_statx) {
        struct statx stx;
        // Only request needed fields (reduces network roundtrips)
        unsigned int mask = STATX_TYPE | STATX_MODE | STATX_SIZE |
                           STATX_UID | STATX_GID | STATX_MTIME;

        if (syscall(SYS_statx, AT_FDCWD, path, 0, mask, &stx) == 0) {
            // Convert statx to stat
            convert_statx_to_stat(&stx, st);
            return 0;
        }
    }

    // Fallback to regular stat
    return stat(path, st);
}
```

### 4. Lock-Free Data Structures

**Strategy**: Use lock-free queues and atomic operations to minimize contention.

```c
#include <stdatomic.h>

// Lock-free ring buffer for work queue
typedef struct {
    void **buffer;
    size_t size;
    atomic_size_t head;
    atomic_size_t tail;
    atomic_int stop;
} LockFreeQueue;

// Lock-free enqueue
int queue_push(LockFreeQueue *q, void *item) {
    size_t head = atomic_load(&q->head);
    size_t next = (head + 1) % q->size;

    // Check if full
    if (next == atomic_load(&q->tail)) {
        return -1;  // Queue full
    }

    q->buffer[head] = item;
    atomic_store(&q->head, next);
    return 0;
}

// Lock-free dequeue with work stealing
void* queue_steal(LockFreeQueue *q) {
    size_t tail = atomic_load(&q->tail);

    if (tail == atomic_load(&q->head)) {
        return NULL;  // Queue empty
    }

    void *item = q->buffer[tail];
    atomic_store(&q->tail, (tail + 1) % q->size);
    return item;
}
```

### 5. Network Filesystem-Specific Optimizations

```c
// Network FS detection and optimization
typedef enum {
    FS_LOCAL,
    FS_NFS,
    FS_LUSTRE,
    FS_WEKA,
    FS_BEEGFS,
    FS_GPFS
} FilesystemType;

FilesystemType detect_filesystem(const char *path) {
    struct statfs fs_stat;
    if (statfs(path, &fs_stat) != 0) return FS_LOCAL;

    switch (fs_stat.f_type) {
        case 0x6969:     return FS_NFS;      // NFS_SUPER_MAGIC
        case 0x0BD00BD0: return FS_LUSTRE;   // LUSTRE_SUPER_MAGIC
        case 0x19830326: return FS_BEEGFS;   // BEEGFS_SUPER_MAGIC
        case 0x47504653: return FS_GPFS;     // GPFS_SUPER_MAGIC
        // Weka detection via mount options
        default:         return detect_weka_fs(path);
    }
}

// Filesystem-specific tuning
void tune_for_filesystem(FilesystemType fs_type, TraversalConfig *config) {
    switch (fs_type) {
        case FS_NFS:
            config->readahead_kb = 1024;      // 1MB readahead
            config->batch_size = 1000;        // Large batches
            config->thread_count = 8;         // Moderate parallelism
            break;

        case FS_LUSTRE:
            config->readahead_kb = 4096;      // 4MB readahead
            config->stripe_aware = 1;         // Respect Lustre striping
            config->thread_count = 32;        // High parallelism
            config->use_o_direct = 1;         // Bypass cache
            break;

        case FS_WEKA:
            config->readahead_kb = 8192;      // 8MB readahead
            config->thread_count = 64;        // Very high parallelism
            config->use_statx = 1;            // Selective metadata
            config->batch_size = 10000;       // Very large batches
            break;

        case FS_BEEGFS:
            config->readahead_kb = 2048;      // 2MB readahead
            config->thread_count = 16;        // Good parallelism
            config->chunk_aware = 1;          // Respect BeeGFS chunks
            break;
    }
}
```

### 6. Adaptive Thread Pool with Work Stealing

```c
// Dynamic thread pool with work stealing
typedef struct {
    WorkerThread *workers;
    int min_threads;
    int max_threads;
    int current_threads;
    atomic_int idle_threads;
    LockFreeQueue **work_queues;  // Per-thread queues
    pthread_mutex_t resize_mutex;
} ThreadPool;

// Work stealing scheduler
void* worker_thread_steal(void *arg) {
    WorkerThread *self = (WorkerThread *)arg;
    ThreadPool *pool = self->pool;

    while (!atomic_load(&pool->stop)) {
        void *work = queue_steal(self->work_queue);

        if (!work) {
            // Try to steal from other threads
            for (int i = 0; i < pool->current_threads; i++) {
                if (i == self->id) continue;

                work = queue_steal(pool->work_queues[i]);
                if (work) break;
            }
        }

        if (work) {
            atomic_fetch_add(&self->stats_processed, 1);
            process_work_item(work);
        } else {
            // Adaptive waiting with exponential backoff
            adaptive_wait(self);
        }
    }

    return NULL;
}

// Dynamic thread scaling based on load
void scale_thread_pool(ThreadPool *pool) {
    int idle = atomic_load(&pool->idle_threads);
    int total = pool->current_threads;

    // Scale up if too few idle threads
    if (idle < 2 && total < pool->max_threads) {
        add_worker_thread(pool);
    }

    // Scale down if too many idle threads
    if (idle > total / 2 && total > pool->min_threads) {
        remove_worker_thread(pool);
    }
}
```

### 7. Memory-Mapped I/O for Result Collection

```c
// Memory-mapped result buffer for zero-copy
typedef struct {
    int fd;
    void *mmap_base;
    size_t mmap_size;
    size_t current_offset;
    pthread_spinlock_t lock;
} MmapResultBuffer;

MmapResultBuffer* create_mmap_buffer(const char *path, size_t size) {
    MmapResultBuffer *buf = malloc(sizeof(MmapResultBuffer));

    // Create file with fallocate for performance
    buf->fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    fallocate(buf->fd, 0, 0, size);

    // Memory map with huge pages if available
    buf->mmap_base = mmap(NULL, size, PROT_READ | PROT_WRITE,
                          MAP_SHARED | MAP_HUGETLB, buf->fd, 0);

    if (buf->mmap_base == MAP_FAILED) {
        // Fallback without huge pages
        buf->mmap_base = mmap(NULL, size, PROT_READ | PROT_WRITE,
                              MAP_SHARED, buf->fd, 0);
    }

    pthread_spin_init(&buf->lock, PTHREAD_PROCESS_PRIVATE);
    return buf;
}

// Lock-free writing with CAS
size_t write_to_mmap(MmapResultBuffer *buf, void *data, size_t len) {
    size_t offset;

    // Atomic reservation of space
    do {
        offset = buf->current_offset;
        if (offset + len > buf->mmap_size) return -1;
    } while (!__sync_bool_compare_and_swap(&buf->current_offset,
                                           offset, offset + len));

    // Copy data to reserved space
    memcpy(buf->mmap_base + offset, data, len);
    return offset;
}
```

### 8. Vectorized Operations with SIMD

```c
#include <immintrin.h>  // For AVX2

// Vectorized string comparison for path matching
int vectorized_path_match(const char *paths[], int count, const char *pattern) {
    __m256i pattern_vec = _mm256_set1_epi8(pattern[0]);

    for (int i = 0; i < count; i += 32) {
        // Load 32 path first characters
        __m256i path_chars = _mm256_loadu_si256((__m256i*)&paths[i]);

        // Compare all at once
        __m256i matches = _mm256_cmpeq_epi8(path_chars, pattern_vec);
        int mask = _mm256_movemask_epi8(matches);

        if (mask) {
            // Found potential matches, check full strings
            return check_full_matches(paths, i, mask, pattern);
        }
    }
    return -1;
}
```

### 9. Io_uring for Asynchronous I/O (Linux 5.1+)

```c
#include <liburing.h>

// Asynchronous stat operations using io_uring
typedef struct {
    struct io_uring ring;
    int queue_depth;
    struct io_uring_sqe *sqes;
    struct io_uring_cqe *cqes;
} AsyncIOContext;

int async_stat_batch(AsyncIOContext *ctx, const char *paths[], int count) {
    struct io_uring_sqe *sqe;

    // Submit batch of stat operations
    for (int i = 0; i < count; i++) {
        sqe = io_uring_get_sqe(&ctx->ring);
        io_uring_prep_statx(sqe, AT_FDCWD, paths[i], 0,
                           STATX_BASIC_STATS, &stat_results[i]);
        io_uring_sqe_set_data(sqe, &paths[i]);
    }

    // Submit all at once
    io_uring_submit(&ctx->ring);

    // Reap completions
    struct io_uring_cqe *cqe;
    for (int i = 0; i < count; i++) {
        io_uring_wait_cqe(&ctx->ring, &cqe);
        process_stat_result(cqe);
        io_uring_cqe_seen(&ctx->ring, cqe);
    }

    return 0;
}
```

### 10. eBPF for Kernel-Level Filtering

```c
// eBPF program for in-kernel path filtering
const char *ebpf_filter_program = R"(
#include <linux/bpf.h>
#include <linux/ptrace.h>

SEC("kprobe/vfs_readdir")
int filter_readdir(struct pt_regs *ctx) {
    char path[256];
    bpf_probe_read_str(&path, sizeof(path), (void *)PT_REGS_PARM1(ctx));

    // Skip .snapshot directories in kernel
    if (path[0] == '.' && __builtin_memcmp(path, ".snapshot", 9) == 0) {
        return -1;  // Skip this entry
    }

    return 0;
}
)";

// Load and attach eBPF filter
int attach_ebpf_filter() {
    // Compile and load eBPF program
    // Attach to vfs_readdir kprobe
    // This filters at kernel level before data reaches userspace
}
```

## Python Integration Strategy

### 1. Zero-Copy Data Transfer

```python
# Python side - using memoryview for zero-copy
import numpy as np
from pwalk._core import traverse_nogil

class ZeroCopyResults:
    def __init__(self, mmap_path):
        self.mmap_file = np.memmap(mmap_path, dtype=self.dtype, mode='r')

    @property
    def dtype(self):
        return np.dtype([
            ('inode', np.uint64),
            ('size', np.uint64),
            ('uid', np.uint32),
            ('gid', np.uint32),
            ('mode', np.uint32),
            ('mtime', np.uint64),
        ])

    def __iter__(self):
        # Iterate without copying data
        return iter(self.mmap_file)
```

### 2. Async/Await Support

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

class AsyncWalker:
    def __init__(self, max_workers=None):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def walk(self, path, **kwargs):
        """Async generator for directory traversal"""
        loop = asyncio.get_event_loop()

        # Run C traversal in thread pool (releases GIL)
        future = loop.run_in_executor(
            self.executor,
            traverse_nogil,
            path,
            kwargs
        )

        # Yield results as they become available
        async for result in self._stream_results(future):
            yield result

    async def _stream_results(self, future):
        """Stream results from C extension"""
        reader = await future
        while True:
            batch = reader.get_next_batch()
            if not batch:
                break
            for item in batch:
                yield item
```

## Performance Benchmarks and Targets

### Expected Performance Metrics

| Filesystem | Files/sec | Throughput | Latency (p99) |
|------------|-----------|------------|---------------|
| Local SSD  | 100,000+  | 1 GB/s     | < 1ms         |
| NFS        | 20,000    | 200 MB/s   | < 10ms        |
| Lustre     | 50,000    | 500 MB/s   | < 5ms         |
| Weka       | 80,000    | 800 MB/s   | < 2ms         |
| BeeGFS     | 40,000    | 400 MB/s   | < 5ms         |

### Optimization Validation

```python
# Benchmark script
def benchmark_traversal(path, config):
    results = {
        'files_scanned': 0,
        'dirs_scanned': 0,
        'bytes_processed': 0,
        'elapsed_time': 0,
        'cpu_time': 0,
        'peak_memory': 0,
    }

    # Run with different configurations
    configs = [
        {'cpu_affinity': False, 'work_stealing': False},
        {'cpu_affinity': True, 'work_stealing': False},
        {'cpu_affinity': True, 'work_stealing': True},
        {'cpu_affinity': True, 'work_stealing': True, 'io_uring': True},
    ]

    for config in configs:
        result = run_benchmark(path, config)
        print(f"Config: {config}")
        print(f"  Files/sec: {result['files_per_sec']:,.0f}")
        print(f"  CPU usage: {result['cpu_percent']:.1f}%")
        print(f"  Memory: {result['memory_mb']:.1f} MB")
```

## Implementation Checklist

### Phase 1: Core GIL-Free Implementation
- [ ] Implement Py_BEGIN_ALLOW_THREADS blocks
- [ ] Create lock-free ring buffer
- [ ] Add atomic counters for statistics
- [ ] Implement zero-copy result transfer

### Phase 2: CPU and NUMA Optimization
- [ ] Add CPU affinity support
- [ ] Implement NUMA-aware memory allocation
- [ ] Create intelligent thread placement
- [ ] Add SMT/hyperthreading awareness

### Phase 3: Kernel-Level Optimizations
- [ ] Implement getdents64 for batch reading
- [ ] Add statx support for selective metadata
- [ ] Implement O_DIRECT for network FS
- [ ] Add custom readahead configuration

### Phase 4: Advanced I/O
- [ ] Implement io_uring support (Linux 5.1+)
- [ ] Add memory-mapped result buffers
- [ ] Create eBPF filters for kernel-level filtering
- [ ] Implement vectorized path operations

### Phase 5: Network FS Tuning
- [ ] Add filesystem type detection
- [ ] Implement per-FS optimization profiles
- [ ] Add Lustre stripe-aware traversal
- [ ] Implement Weka-specific optimizations

### Phase 6: Python Integration
- [ ] Create async/await interface
- [ ] Implement zero-copy numpy integration
- [ ] Add progress callbacks
- [ ] Create comprehensive benchmarking suite

## Configuration API

```python
from pwalk import Walker, Config

config = Config(
    # Thread management
    min_threads=4,
    max_threads=64,
    work_stealing=True,

    # CPU optimization
    cpu_affinity=True,
    numa_aware=True,
    prefer_local_numa=True,

    # Kernel optimization
    use_getdents=True,
    use_statx=True,
    use_o_direct=True,
    readahead_kb=4096,

    # I/O optimization
    use_io_uring=True,
    use_mmap=True,
    buffer_size_mb=1024,

    # Network FS
    detect_filesystem=True,
    auto_tune=True,
)

walker = Walker(config)
for dirpath, dirnames, filenames in walker.walk('/data'):
    process(dirpath, dirnames, filenames)
```

## Monitoring and Profiling

```python
# Real-time performance monitoring
from pwalk import Monitor

monitor = Monitor()
monitor.start()

for result in pwalk.walk('/data'):
    # Process results
    pass

stats = monitor.stop()
print(f"Files/sec: {stats.files_per_second:,.0f}")
print(f"Throughput: {stats.mb_per_second:.1f} MB/s")
print(f"CPU usage: {stats.cpu_percent:.1f}%")
print(f"Memory: {stats.memory_mb:.1f} MB")
print(f"Thread efficiency: {stats.thread_efficiency:.1%}")
```

## Security Considerations

- All optimizations maintain security boundaries
- No privilege escalation through CPU affinity
- eBPF programs run in verified sandbox
- Memory-mapped files respect file permissions
- NUMA optimizations don't leak across security domains

## Compatibility Matrix

| Feature | Linux 5.0+ | Linux 4.x | macOS | Windows |
|---------|------------|-----------|-------|---------|
| Basic threading | ✓ | ✓ | ✓ | ✗ |
| CPU affinity | ✓ | ✓ | ✗ | ✗ |
| NUMA support | ✓ | ✓ | ✗ | ✗ |
| getdents64 | ✓ | ✓ | ✗ | ✗ |
| statx | ✓ | ✗ | ✗ | ✗ |
| io_uring | ✓ (5.1+) | ✗ | ✗ | ✗ |
| eBPF filtering | ✓ (4.x+) | Partial | ✗ | ✗ |
| Huge pages | ✓ | ✓ | ✗ | ✗ |

## References

- [io_uring documentation](https://kernel.dk/io_uring.pdf)
- [Linux kernel statx(2)](https://man7.org/linux/man-pages/man2/statx.2.html)
- [NUMA API](https://man7.org/linux/man-pages/man3/numa.3.html)
- [eBPF for filesystem](https://lwn.net/Articles/747551/)
- [Lock-free programming](https://preshing.com/20120612/an-introduction-to-lock-free-programming/)