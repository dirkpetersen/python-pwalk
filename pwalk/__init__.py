"""
python-pwalk: High-performance parallel filesystem walker

A parallel replacement for os.walk() optimized for massive filesystems.
Based on John Dey's pwalk C implementation.
"""

from .walk import walk
from .report import report
from .repair import repair

__version__ = "0.1.6"
__all__ = ["walk", "report", "repair"]
