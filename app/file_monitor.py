"""
File Monitor Module - Cross-platform file opening and change detection

This module provides functionality to open files with their default or 
specified applications, monitor the application process lifecycle, and 
detect file modifications using hash comparison and filesystem events.

Key features:
- Opens files in new application instances (macOS)
- Removes macOS quarantine attributes automatically
- Tracks application processes with intelligent filtering
- Detects file changes via MD5 hashing
- Cross-platform support (macOS, Linux)

Example usage:
    from file_monitor import open_and_monitor

    # Open with default app
    changed = open_and_monitor('/path/to/document.docx')

    # Force specific application
    changed = open_and_monitor('/path/to/document.docx', 
                               app_name='Microsoft Word')

    if changed:
        print("File was modified")
"""

import subprocess
import platform
import os
import time
import psutil
import hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

READONLY_EXTENSIONS = ('.eml', '.msg', '.pdf')

__all__ = [
    'open_and_monitor',
    'remove_quarantine',
    'get_file_hash',
    'FileChangeHandler'
]

__version__ = '1.0.0'


class FileChangeHandler(FileSystemEventHandler):
    """Monitors filesystem events for a specific file"""

    def __init__(self, filepath):
        """
        Initialize file change handler

        Args:
            filepath: Absolute path to file to monitor
        """
        self.filepath = os.path.abspath(filepath)
        self.event_count = 0

    def on_any_event(self, event):
        """Handle filesystem events"""
        event_path = os.path.abspath(event.src_path)
        if not event.is_directory and event_path == self.filepath:
            if event.event_type in ['modified', 'closed']:
                self.event_count += 1


def remove_quarantine(filepath):
    """
    Remove macOS quarantine attribute from file

    The quarantine attribute is set by macOS on downloaded files and can
    prevent applications from opening them without user confirmation.

    Args:
        filepath: Path to file to process

    Note:
        Only operates on macOS (Darwin) systems. No-op on other platforms.
    """
    if platform.system() != 'Darwin':
        return

    try:
        result = subprocess.run(
            ['xattr', filepath],
            capture_output=True,
            text=True
        )

        if 'com.apple.quarantine' in result.stdout:
            print(f"Removing quarantine attribute from: {os.path.basename(
                filepath)}")
            subprocess.run(
                ['xattr', '-d', 'com.apple.quarantine', filepath],
                check=True,
                stderr=subprocess.DEVNULL
            )
            print("✓ Quarantine attribute removed")
    except subprocess.CalledProcessError:
        print("⚠ Could not remove quarantine attribute")
    except FileNotFoundError:
        pass


def get_file_hash(filepath):
    """
    Calculate MD5 hash of file content

    Args:
        filepath: Path to file

    Returns:
        str: Hexadecimal MD5 hash, or None if file cannot be read
    """
    try:
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def is_system_or_helper_process(proc_name):
    """
    Check if process is a system utility or helper process

    Args:
        proc_name: Process name to check

    Returns:
        bool: True if process should be filtered out
    """
    proc_lower = proc_name.lower()

    excluded_keywords = [
        'helper', 'xpc', 'daemon', 'service',
        'mobileasset', 'managedclient', 'mdm',
        'system_profiler', 'profiler', 'gpu',
        'renderer', 'decoder', 'encoder',
        'crash', 'reporter', 'sync', 'autoupdate',
        'licensing', 'plugin', 'vtdecoder'
    ]

    return any(keyword in proc_lower for keyword in excluded_keywords)


def get_app_processes_by_file(filepath, baseline_pids):
    """
    Find application processes that have the specified file open

    Args:
        filepath: Path to file
        baseline_pids: Set of PIDs that existed before opening file

    Returns:
        list: PIDs of processes with file open
    """
    app_pids = []
    current_pids = set(p.pid for p in psutil.process_iter()) - baseline_pids

    print(f"\n=== Checking {len(
            current_pids)} new processes for file access ===")

    for pid in current_pids:
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()

            if proc_name.lower() in ['open', 'sh', 'bash', 'zsh']:
                print(f"  [SKIPPED] PID {pid}: {proc_name} (shell/utility)")
                continue

            print(f"  [CHECKING] PID {pid}: {proc_name}")

            try:
                for item in proc.open_files():
                    if os.path.abspath(item.path) == os.path.abspath(filepath):
                        print("    ✓ Has file open!")
                        app_pids.append(pid)
                        break
            except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
                print(f"    ✗ Cannot access open files: {type(e).__name__}")
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  [ERROR] PID {pid}: {type(e).__name__}")
            pass

    return app_pids


def get_new_app_processes(baseline_pids, app_name_hint=None):
    """
    Find main application process with intelligent filtering

    Automatically detects the main application process by filtering out
    shell utilities, system processes, and helper processes.

    Args:
        baseline_pids: Set of PIDs that existed before opening file
        app_name_hint: Optional application name to prioritize in detection

    Returns:
        list: PIDs of main application process(es)
    """
    current_pids = set(p.pid for p in psutil.process_iter()) - baseline_pids

    print(f"\n=== Finding main application from {len(
        current_pids)} new processes ===")
    if app_name_hint:
        print(f"App name hint: {app_name_hint}")

    main_app_candidates = []

    for pid in current_pids:
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            name_lower = name.lower()

            if any(x in name_lower for x in [
                    'open', 'sh', 'bash', 'zsh', 'xdg-', 'dbus']):
                print(f"  [FILTERED] PID {pid}: {name} (shell process)")
                continue

            if is_system_or_helper_process(name):
                print(f"  [FILTERED] PID {pid}: {name} (system/helper process)")  # noqa
                continue

            print(f"  [CANDIDATE] PID {pid}: {name}")

            if app_name_hint and app_name_hint.lower() in name_lower:
                print(f"    ✓ Matches app hint '{app_name_hint}'")
                main_app_candidates.insert(0, (pid, name))
            else:
                main_app_candidates.append((pid, name))

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  [ERROR] PID {pid}: {type(e).__name__}")
            pass

    if main_app_candidates:
        pid, name = main_app_candidates[0]
        print(f"\n✓ Selected main app: {name} (PID {pid})")
        return [pid]

    return []


def open_and_monitor(filepath, app_name=None, verbose=True):
    """
    Open file with application and monitor for changes

    Opens the specified file with its default application (or a specified app),
    monitors the application process, and detects whether the file was modified
    during the session using MD5 hash comparison.

    Args:
        filepath: Path to file to open
        app_name: Optional application name to force specific app (macOS only)
                 Examples: 'Microsoft Word', 'ONLYOFFICE', 'TextEdit'
        verbose: Enable detailed console output (default: True)

    Returns:
        bool: True if file content changed, False otherwise

    Raises:
        FileNotFoundError: If specified file does not exist
        NotImplementedError: If platform is not supported

    Example:
        >>> changed = open_and_monitor('/path/to/document.docx')
        >>> if changed:
        ...     print("File was modified")

        >>> changed = open_and_monitor(
        ...     '/path/to/document.docx',
        ...     app_name='Microsoft Word'
        ... )
    """
    filepath = os.path.abspath(filepath)

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    if verbose:
        print(f"Opening file: {filepath}")
        if app_name:
            print(f"Forced app: {app_name}")

    remove_quarantine(filepath)

    initial_hash = get_file_hash(filepath)
    if verbose:
        print(f"Initial hash: {initial_hash}")

    baseline_pids = set(p.pid for p in psutil.process_iter())
    if verbose:
        print(f"Baseline processes: {len(baseline_pids)} PIDs")

    handler = FileChangeHandler(filepath)
    observer = Observer()
    watch_dir = os.path.dirname(filepath) or '.'
    observer.schedule(handler, path=watch_dir, recursive=False)
    observer.start()

    system = platform.system()
    if system == 'Darwin':
        if app_name:
            cmd = ['open', '-n', '-a', app_name, filepath]
        else:
            cmd = ['open', '-n', filepath]
    elif system == 'Linux':
        cmd = ['xdg-open', filepath]
    else:
        raise NotImplementedError(f"Platform {system} not supported")

    if verbose:
        print(f"\nExecuting command: {' '.join(cmd)}")

    try:
        subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
        
        # Do not wait for read only files
        if filepath.lower().endswith(READONLY_EXTENSIONS):
            return False

        if system == 'Darwin':
            if verbose:
                print("\nWaiting 1.5s for application to spawn...")
            time.sleep(1.5)

            app_pids = get_app_processes_by_file(filepath, baseline_pids)

            if not app_pids:
                if verbose:
                    print("\n⚠ No processes found with file open, "
                          "trying alternative method...")
                app_pids = get_new_app_processes(baseline_pids, app_name)

            if app_pids:
                if verbose:
                    print(f"\n✓ Monitoring {len(app_pids)} process(es):")
                    for pid in app_pids:
                        try:
                            proc = psutil.Process(pid)
                            print(f"  - PID {pid}: {proc.name()}")
                        except Exception:
                            print(f"  - PID {pid}: <process ended>")

                    print("\nWaiting for application to close...")

                while any(psutil.pid_exists(pid) for pid in app_pids):
                    time.sleep(0.5)

                if verbose:
                    print("\n✓ Application closed")
            else:
                if verbose:
                    print("\n⚠ Warning: Could not identify application "
                          "process. Waiting 5 seconds...")
                time.sleep(5)

        else:  # Linux
            time.sleep(1)
            new_pids = set(p.pid for p in psutil.process_iter(
                )) - baseline_pids
            app_pids = []

            for pid in new_pids:
                try:
                    p = psutil.Process(pid)
                    name = p.name().lower()
                    if not any(x in name for x in [
                            'xdg-', 'sh', 'bash', 'dbus']):
                        app_pids.append(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if app_pids:
                while any(psutil.pid_exists(pid) for pid in app_pids):
                    time.sleep(0.5)
            else:
                time.sleep(2)

    finally:
        time.sleep(1)
        observer.stop()
        observer.join()

    final_hash = get_file_hash(filepath)
    if verbose:
        print(f"\nFinal hash: {final_hash}")

    return initial_hash != final_hash


if __name__ == '__main__':
    print("File Monitor Module")
    print(f"Version: {__version__}")
    print("\nThis is a library module. Import it in your Python scripts.")
    print("\nExample usage:")
    print("    from file_monitor import open_and_monitor")
    print("    changed = open_and_monitor('/path/to/file.docx')")
