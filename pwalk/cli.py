#!/usr/bin/env python
"""
Command-line interface for pwalk
"""

import argparse
import sys
import os
from pathlib import Path

from . import __version__
from .walk import walk
from .report import report
from .repair import repair


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='High-performance parallel filesystem walker',
        epilog='Based on John Dey\'s pwalk. See README for more information.'
    )

    parser.add_argument('--version', action='version', version=f'pwalk {__version__}')

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Walk command
    walk_parser = subparsers.add_parser('walk', help='Walk filesystem (streaming)')
    walk_parser.add_argument('path', help='Starting directory path')
    walk_parser.add_argument('--max-threads', type=int, help='Maximum threads')
    walk_parser.add_argument('--no-snapshots', action='store_false', dest='ignore_snapshots',
                            help='Include .snapshot directories (default: skip)')

    # Report command
    report_parser = subparsers.add_parser('report', help='Generate filesystem metadata report')
    report_parser.add_argument('path', help='Starting directory path')
    report_parser.add_argument('--format', choices=['parquet', 'csv'], default='parquet',
                              help='Output format (default: parquet)')
    report_parser.add_argument('--output', '-o', help='Output file path')
    report_parser.add_argument('--max-threads', type=int, help='Maximum threads')

    # Repair command
    repair_parser = subparsers.add_parser('repair', help='Repair filesystem permissions')
    repair_parser.add_argument('path', help='Starting directory path')
    repair_parser.add_argument('--dry-run', action='store_true',
                              help='Show changes without applying them')
    repair_parser.add_argument('--change-gids', help='Comma-separated list of GIDs to treat as private groups')
    repair_parser.add_argument('--force-group-writable', action='store_true',
                              help='Ensure all files/dirs have group read+write')
    repair_parser.add_argument('--exclude', action='append', help='Paths to exclude (can specify multiple)')
    repair_parser.add_argument('--max-threads', type=int, help='Maximum threads')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == 'walk':
            # Simple walk and print
            for dirpath, dirnames, filenames in walk(
                args.path,
                max_threads=args.max_threads,
                ignore_snapshots=args.ignore_snapshots
            ):
                print(f"{dirpath}: {len(dirnames)} dirs, {len(filenames)} files")

        elif args.command == 'report':
            print(f"Generating {args.format.upper()} report for {args.path}...")

            output_path, errors = report(
                args.path,
                format=args.format,
                output=args.output,
                max_threads=args.max_threads
            )

            print(f"\nReport saved to: {output_path}")
            print(f"Files processed: (see report)")
            print(f"Errors: {len(errors)}")

            if errors:
                print("\nSample errors (first 10):")
                for error in errors[:10]:
                    print(f"  {error}")
                if len(errors) > 10:
                    print(f"  ... and {len(errors) - 10} more")

        elif args.command == 'repair':
            if not args.dry_run and os.geteuid() != 0:
                print("ERROR: repair command must be run as root (use sudo)")
                print("       or use --dry-run to preview changes")
                return 1

            change_gids = []
            if args.change_gids:
                change_gids = [int(gid.strip()) for gid in args.change_gids.split(',')]

            repair(
                args.path,
                dry_run=args.dry_run,
                change_gids=change_gids if change_gids else None,
                force_group_writable=args.force_group_writable,
                exclude=args.exclude,
                max_threads=args.max_threads
            )

        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
