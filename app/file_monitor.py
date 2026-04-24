import subprocess
import platform
import os
import hashlib
import tkinter as tk
import json
import sys


READONLY_EXTENSIONS = ('.eml', '.msg', '.pdf')

__all__ = [
    'open_and_monitor',
    'remove_quarantine',
    'get_file_hash',
]

__version__ = '1.3.0'


def _visible_frame():
    """Returns {'x', 'bottom', 'w', 'h'} of usable screen area via JXA."""
    script = '''ObjC.import('AppKit');
var s = $.NSScreen.mainScreen;
var v = s.visibleFrame;
var f = s.frame;
JSON.stringify({x:v.origin.x,y:v.origin.y,w:v.size.width,h:v.size.height,fh:f.size.height});'''  # noqa
    try:
        r = subprocess.run(['osascript', '-l', 'JavaScript', '-e', script],
                           capture_output=True, text=True, timeout=3)
        d = json.loads(r.stdout.strip())
        # Cocoa uses bottom-left origin; convert to Tkinter top-left
        return {'x': d['x'], 'bottom': d['fh'] - d['y'],
                'w': d['w'], 'h': d['h']}
    except Exception:
        return None


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
            print(f"Removing quarantine attribute from: {os.path.basename(filepath)}")  # noqa
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


def _show_confirmation_dialog(filepath=""):
    confirmed = [False]
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    sh = root.winfo_screenheight()

    d = tk.Toplevel(root)
    d.title("DodatekEZD")
    d.resizable(False, False)
    d.attributes('-topmost', True)

    f = tk.Frame(d)
    f.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

    b = tk.Frame(f)
    b.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))
    c = tk.Frame(b)
    c.pack(anchor=tk.CENTER)

    def no():
        confirmed[0] = False
        d.destroy()
        root.destroy()

    def yes():
        confirmed[0] = True
        d.destroy()
        root.destroy()

    tk.Button(c, text="Anuluj", command=no).pack(side=tk.LEFT, padx=(0, 6))
    confirm_btn = tk.Button(
        c, text="Potwierdzam", command=yes, default=tk.ACTIVE)
    confirm_btn.pack(side=tk.LEFT, padx=(0, 6))

    m = tk.Frame(f)
    m.pack(fill=tk.BOTH, expand=True)
    tk.Label(m, text="Potwierdź zapisanie zmian w pliku",
             wraplength=360, anchor=tk.CENTER,
             justify=tk.CENTER).pack(expand=True)

    d.protocol("WM_DELETE_WINDOW", no)
    d.bind('<Return>', lambda e: yes())
    d.bind('<Escape>', lambda e: no())

    d.update_idletasks()
    W = d.winfo_reqwidth() 
    H = d.winfo_reqheight()
    if sys.platform == "darwin":
        MARGIN_X = 0
        MARGIN_Y = 32
    else:
        MARGIN_X = 0
        MARGIN_Y = 0

    vf = _visible_frame() if sys.platform == "darwin" else None
    if vf:
        x = vf['x'] + MARGIN_X
        y = vf['bottom'] - H - MARGIN_Y
    else:
        x, y = MARGIN_X, sh - H - MARGIN_Y - 48

    d.geometry(f"{W}x{H}+{int(x)}+{int(y)}")
    d.minsize(W, H)
    d.grab_set()
    d.focus_force()
    confirm_btn.focus_set()

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
