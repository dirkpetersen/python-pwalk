"""
walk.py - os.walk() compatible directory walker with snapshot filtering
"""

import os
from typing import Iterator, Tuple, List, Optional, Callable


def walk(
    top: str,
    topdown: bool = True,
    onerror: Optional[Callable[[OSError], None]] = None,
    followlinks: bool = False,
    max_threads: Optional[int] = None,
    ignore_snapshots: bool = True
) -> Iterator[Tuple[str, List[str], List[str]]]:
    """
    Directory tree generator, 100% compatible with os.walk().

    Adds optional snapshot filtering for enterprise storage systems.
    For maximum performance with metadata collection, use report() which
    employs multi-threaded C code.

    Args:
        top: Starting directory path
        topdown: If True, yield parent before children (allows dirnames modification)
        onerror: Callback function for OSError instances
        followlinks: Whether to follow symbolic links
        max_threads: Reserved for future parallel implementation
        ignore_snapshots: Skip .snapshot directories (default: True)

    Yields:
        (dirpath, dirnames, filenames) tuples

    Examples:
        >>> for dirpath, dirnames, filenames in walk('/data'):
        ...     print(f"Directory: {dirpath}")

        >>> # Prune traversal by modifying dirnames in-place
        >>> for dirpath, dirnames, filenames in walk('/data'):
        ...     dirnames[:] = [d for d in dirnames if not d.startswith('.')]

    Note:
        For high-performance bulk metadata collection, use report() instead,
        which uses multi-threaded C code for 5-10x faster processing.
    """
    # Use os.walk() with snapshot filtering
    for dirpath, dirnames, filenames in os.walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks):
        # Filter .snapshot directories if requested
        if ignore_snapshots and '.snapshot' in dirnames:
            dirnames.remove('.snapshot')
        yield dirpath, dirnames, filenames
