#!/usr/bin/env python
"""
Setup script for python-pwalk
"""

import os
import sys
import subprocess
from setuptools import setup, Extension
from pathlib import Path

# Read long description
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

# Check for zstd library (silently)
has_zstd = False
try:
    result = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True, timeout=5)
    has_zstd = 'libzstd' in result.stdout
except:
    try:
        result = subprocess.run(['pkg-config', '--exists', 'libzstd'], capture_output=True, timeout=5)
        has_zstd = result.returncode == 0
    except:
        pass

# Configure C extension
pwalk_core_args = {
    'sources': ['src/pwalk_ext/pwalk_core.c'],
    'libraries': ['pthread'],
    'extra_compile_args': ['-O3', '-pthread', '-march=native', '-D_GNU_SOURCE'],
    'extra_link_args': ['-pthread'],
}

if has_zstd:
    pwalk_core_args['libraries'].append('zstd')
    pwalk_core_args['extra_compile_args'].append('-DHAVE_ZSTD')

pwalk_core = Extension('_pwalk_core', **pwalk_core_args)

setup(
    name='pwalk',
    version='0.1.0',
    author='Dirk Petersen',
    author_email='dp@nowhere.com',
    description='High-performance parallel filesystem walker with zero dependencies',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/dirkpetersen/python-pwalk',
    packages=['pwalk'],
    ext_modules=[pwalk_core],
    python_requires='>=3.10',
    install_requires=[
        # No required dependencies - pure performance!
    ],
    extras_require={
        'dev': [
            'pytest>=7.0.0',
            'pytest-cov>=4.0.0',
            'pytest-benchmark>=4.0.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'pwalk=pwalk.cli:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: System Administrators',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Topic :: System :: Filesystems',
        'Topic :: System :: Systems Administration',
        'License :: OSI Approved :: GNU General Public License v2 (GPLv2)',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Programming Language :: C',
        'Operating System :: POSIX :: Linux',
    ],
    keywords='filesystem walk parallel performance hpc hpc-tools storage-analysis',
    project_urls={
        'Bug Reports': 'https://github.com/dirkpetersen/python-pwalk/issues',
        'Source': 'https://github.com/dirkpetersen/python-pwalk',
        'Documentation': 'https://github.com/dirkpetersen/python-pwalk',
        'Changelog': 'https://github.com/dirkpetersen/python-pwalk/releases',
    },
)
