"""
repair.py - Filesystem permission repair operations
"""

import os
import grp
import pwd
import syslog
import stat
from typing import List, Optional
from .walk import walk


# Protected system paths that cannot be modified
PROTECTED_PATHS = [
    '/', '/bin', '/boot', '/dev', '/etc', '/lib',
    '/lib64', '/proc', '/root', '/sbin', '/sys', '/usr'
]


def validate_gid(gid: int) -> bool:
    """Validate GID against /etc/group."""
    try:
        grp.getgrgid(gid)
        return True
    except KeyError:
        syslog.syslog(syslog.LOG_WARNING, f"pwalk: Invalid GID {gid} not in /etc/group")
        return False


def is_protected_path(path: str) -> bool:
    """Check if path is protected system directory."""
    for protected in PROTECTED_PATHS:
        if path.startswith(protected):
            syslog.syslog(syslog.LOG_WARNING, f"pwalk: Attempted modification of protected path: {path}")
            return True
    return False


def find_parent_group(path: str, change_gids: List[int], exclude: List[str]) -> Optional[int]:
    """
    Find appropriate group by walking up directory tree.

    Returns first non-private group (where gid != uid and gid not in change_gids).
    """
    current = os.path.dirname(path)

    while current and current != '/':
        if any(current.startswith(ex) for ex in exclude):
            current = os.path.dirname(current)
            continue

        try:
            st = os.stat(current)

            # Check if this is a non-private group
            if st.st_gid != st.st_uid and st.st_gid not in change_gids and st.st_gid != 0:
                return st.st_gid

        except OSError:
            pass

        current = os.path.dirname(current)

    return None


def repair(
    top: str,
    dry_run: bool = True,
    change_gids: Optional[List[int]] = None,
    force_group_writable: bool = False,
    exclude: Optional[List[str]] = None,
    max_threads: Optional[int] = None
) -> None:
    """
    Repair filesystem permissions in shared folders.

    Args:
        top: Starting directory path
        dry_run: If True, only show changes without making them
        change_gids: List of GIDs to treat like private groups (change to parent group)
        force_group_writable: Ensure all files/dirs have group read+write+execute
        exclude: List of paths to skip
        max_threads: Maximum threads for traversal

    Security:
        - Must be run as root for most operations
        - Validates GIDs against /etc/group
        - Protects system directories
        - Logs all changes to syslog

    Examples:
        >>> # Dry run to preview changes
        >>> repair('/shared', dry_run=True, change_gids=[1234, 5678])

        >>> # Apply changes with group writable
        >>> repair('/shared', dry_run=False, force_group_writable=True)
    """
    if change_gids is None:
        change_gids = []

    if exclude is None:
        exclude = []

    # Check if running as root
    if os.geteuid() != 0 and not dry_run:
        raise PermissionError("repair() must be run as root to modify filesystem")

    # Check protected paths
    if is_protected_path(top):
        raise ValueError(f"Cannot modify protected path: {top}")

    # Validate all GIDs
    for gid in change_gids:
        if not validate_gid(gid):
            raise ValueError(f"Invalid GID: {gid}")

    # Open syslog
    syslog.openlog('pwalk-repair', syslog.LOG_PID, syslog.LOG_USER)

    changes_made = 0
    errors = []

    def should_exclude(path: str) -> bool:
        return any(path.startswith(ex) for ex in exclude)

    try:
        for dirpath, dirnames, filenames in walk(top, max_threads=max_threads):
            if should_exclude(dirpath):
                dirnames.clear()  # Don't recurse
                continue

            # Process directory
            try:
                st = os.stat(dirpath)

                # Check if directory needs setgid bit
                if stat.S_ISDIR(st.st_mode) and not (st.st_mode & stat.S_ISGID):
                    if dry_run:
                        print(f"[DRY-RUN] Would set setgid on: {dirpath}")
                    else:
                        os.chmod(dirpath, st.st_mode | stat.S_ISGID)
                        syslog.syslog(syslog.LOG_INFO, f"Set setgid: {dirpath}")
                    changes_made += 1

                # Check if group needs changing (private group or in change_gids list)
                if st.st_gid == st.st_uid or st.st_gid in change_gids or st.st_gid == 0:
                    new_gid = find_parent_group(dirpath, change_gids, exclude)

                    if new_gid:
                        if dry_run:
                            print(f"[DRY-RUN] Would change group on {dirpath}: {st.st_gid} -> {new_gid}")
                        else:
                            os.chown(dirpath, -1, new_gid)
                            syslog.syslog(syslog.LOG_INFO,
                                         f"Changed group: {dirpath}: {st.st_gid} -> {new_gid}")
                        changes_made += 1
                    else:
                        errors.append(f"Could not find suitable group for: {dirpath}")

                # Ensure group permissions
                if force_group_writable:
                    if stat.S_ISDIR(st.st_mode):
                        needed = stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
                    else:
                        needed = stat.S_IRGRP | stat.S_IWGRP

                    if (st.st_mode & needed) != needed:
                        new_mode = st.st_mode | needed

                        if dry_run:
                            print(f"[DRY-RUN] Would change permissions on {dirpath}: " +
                                  f"{oct(st.st_mode)} -> {oct(new_mode)}")
                        else:
                            os.chmod(dirpath, new_mode)
                            syslog.syslog(syslog.LOG_INFO,
                                         f"Changed permissions: {dirpath}: " +
                                         f"{oct(st.st_mode)} -> {oct(new_mode)}")
                        changes_made += 1

            except OSError as e:
                errors.append(f"Error processing {dirpath}: {e}")

            # Process files
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)

                try:
                    st = os.lstat(filepath)

                    # Skip symlinks
                    if stat.S_ISLNK(st.st_mode):
                        continue

                    # Check if group needs changing
                    if st.st_gid == st.st_uid or st.st_gid in change_gids or st.st_gid == 0:
                        new_gid = find_parent_group(filepath, change_gids, exclude)

                        if new_gid:
                            if dry_run:
                                print(f"[DRY-RUN] Would change group on {filepath}: " +
                                      f"{st.st_gid} -> {new_gid}")
                            else:
                                os.chown(filepath, -1, new_gid)
                                syslog.syslog(syslog.LOG_INFO,
                                             f"Changed group: {filepath}: {st.st_gid} -> {new_gid}")
                            changes_made += 1

                    # Ensure group permissions
                    if force_group_writable:
                        needed = stat.S_IRGRP | stat.S_IWGRP
                        if (st.st_mode & needed) != needed:
                            new_mode = st.st_mode | needed

                            if dry_run:
                                print(f"[DRY-RUN] Would change permissions on {filepath}: " +
                                      f"{oct(st.st_mode)} -> {oct(new_mode)}")
                            else:
                                os.chmod(filepath, new_mode)
                                syslog.syslog(syslog.LOG_INFO,
                                             f"Changed permissions: {filepath}: " +
                                             f"{oct(st.st_mode)} -> {oct(new_mode)}")
                            changes_made += 1

                except OSError as e:
                    errors.append(f"Error processing {filepath}: {e}")

    finally:
        syslog.closelog()

    # Print summary
    mode_str = "DRY-RUN" if dry_run else "APPLIED"
    print(f"\n{mode_str} Summary:")
    print(f"  Changes {'would be' if dry_run else 'were'} made: {changes_made}")
    print(f"  Errors: {len(errors)}")

    if errors:
        print("\nErrors:")
        for error in errors[:10]:  # Show first 10
            print(f"  {error}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")
