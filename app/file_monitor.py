import subprocess
import platform
import os
import hashlib
import tkinter as tk
from tkinter import ttk

READONLY_EXTENSIONS = ('.eml', '.msg', '.pdf')

__all__ = [
    'open_and_monitor',
    'remove_quarantine',
    'get_file_hash',
]

__version__ = '1.2.0'


def remove_quarantine(filepath):
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
    try:
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def _show_confirmation_dialog():
    confirmed = [False]

    root = tk.Tk()
    root.withdraw()

    dialog = tk.Toplevel(root)
    dialog.title("DodatekEZD – Potwierdzenie")
    dialog.resizable(False, False)
    dialog.grab_set()
    dialog.attributes('-topmost', True)

    width, height = 300, 110
    # screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    margin = 16
    x = margin
    y = screen_h - height - margin - 48
    dialog.geometry(f"{width}x{height}+{x}+{y}")

    frame = ttk.Frame(dialog, padding=(14, 10, 14, 12))
    frame.pack(fill=tk.BOTH, expand=True)

    label = ttk.Label(
        frame,
        text="Potwierdź, że zakończyłeś edycję pliku\ni chcesz przesłać zmiany?",
        justify=tk.CENTER,
        wraplength=268,
        font=("TkDefaultFont", 9),
    )
    label.pack(pady=(0, 10))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack()

    def on_confirm():
        confirmed[0] = True
        dialog.destroy()
        root.destroy()

    def on_cancel():
        confirmed[0] = False
        dialog.destroy()
        root.destroy()

    dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    btn_confirm = ttk.Button(btn_frame, text="Potwierdzam", command=on_confirm)
    btn_confirm.pack(side=tk.LEFT, padx=(0, 6))

    btn_cancel = ttk.Button(btn_frame, text="Anuluj", command=on_cancel)
    btn_cancel.pack(side=tk.LEFT)

    btn_confirm.focus_set()
    dialog.bind('<Return>', lambda e: on_confirm())
    dialog.bind('<Escape>', lambda e: on_cancel())

    root.mainloop()

    return confirmed[0]


def open_and_monitor(filepath, verbose=True):
    filepath = os.path.abspath(filepath)

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    if verbose:
        print(f"Opening file: {filepath}")

    remove_quarantine(filepath)

    initial_hash = get_file_hash(filepath)
    if verbose:
        print(f"Initial hash: {initial_hash}")

    system = platform.system()
    if system == 'Darwin':
        cmd = ['open', filepath]
    elif system == 'Linux':
        cmd = ['xdg-open', filepath]
    else:
        raise NotImplementedError(f"Platform {system} not supported")

    if verbose:
        print(f"\nExecuting command: {' '.join(cmd)}")

    if filepath.lower().endswith(READONLY_EXTENSIONS):
        subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
        return False

    subprocess.Popen(cmd, stderr=subprocess.DEVNULL)

    if verbose:
        print("\nWaiting for user confirmation dialog...")

    confirmed = _show_confirmation_dialog()

    if not confirmed:
        if verbose:
            print("✗ User cancelled — returning original hash (no change)")
        return False

    if verbose:
        print("✓ User confirmed — proceeding to hash check")

    final_hash = get_file_hash(filepath)
    if verbose:
        print(f"\nFinal hash: {final_hash}")

    return initial_hash != final_hash


if __name__ == '__main__':
    print("File Monitor Module")
    print(f"Version: {__version__}")
    print("\nThis is a library module. Import it in your Python scripts.")
