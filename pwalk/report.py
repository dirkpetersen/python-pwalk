"""
report.py - Filesystem metadata reporting

Generates CSV reports compatible with John Dey's pwalk format.
Supports zstd compression for 8-10x size reduction.
"""

import os
from typing import Tuple, List, Optional

try:
    import _pwalk_core
    HAS_CORE = True
    HAS_ZSTD = _pwalk_core.HAS_ZSTD
except ImportError:
    HAS_CORE = False
    HAS_ZSTD = False


def report(
    top: str,
    output: Optional[str] = None,
    max_threads: Optional[int] = None,
    compress: str = 'auto'
) -> Tuple[str, List[str]]:
    """
    Generate filesystem metadata report in CSV format.

    Output format is 100% compatible with John Dey's pwalk CSV format.
    Supports zstd compression for fast, efficient storage.

    Args:
        top: Starting directory path
        output: Output file path (default: scan.csv or scan.csv.zst)
        max_threads: Maximum threads (default: SLURM_CPUS_ON_NODE or cpu_count())
        compress: Compression mode - 'auto', 'zstd', 'none'

    Returns:
        (output_path, error_list) tuple

    Examples:
        >>> output, errors = report('/data')
        >>> print(f"Report: {output}")

        >>> # Use with DuckDB
        >>> import duckdb
        >>> df = duckdb.connect().execute(f"SELECT * FROM '{output}'").fetchdf()
    """
    if max_threads is None:
        max_threads = int(os.environ.get('SLURM_CPUS_ON_NODE', os.cpu_count() or 4))

    use_compress = False
    if compress == 'auto':
        use_compress = HAS_ZSTD
    elif compress == 'zstd':
        if not HAS_ZSTD:
            raise ValueError("zstd compression not available. Use compress='auto' or compress='none' instead.")
        use_compress = True
    elif compress == 'none':
        use_compress = False
    else:
        raise ValueError(f"Invalid compress: {compress}. Use 'auto', 'zstd', or 'none'")

    if output is None:
        output = 'scan.csv.zst' if use_compress else 'scan.csv'
    elif use_compress and not output.endswith('.zst'):
        output = output + '.zst'

    if not HAS_CORE:
        raise ImportError("C extension not available. Build with: python setup.py build_ext --inplace")

    try:
        result = _pwalk_core.write_csv(
            top,
            output.replace('.zst', ''),
            max_threads,
            1,  # ignore_snapshots
            1 if use_compress else 0
        )
        return result['output'], []
    except Exception as e:
        raise RuntimeError(f"Failed to generate report: {e}")
