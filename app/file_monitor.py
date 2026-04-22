import subprocess
import platform
import os
import hashlib
import tkinter as tk


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
    d.configure(bg="#f8f9fa")
    
    W, H, M = 520, 170, 16
    d.geometry(f"{W}x{H}+{M}+{sh-H-M-48}")
    
    f = tk.Frame(d, bg="#f8f9fa")
    f.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
    
    b = tk.Frame(f, bg="#f8f9fa", height=34)
    b.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
    b.pack_propagate(False)
    c = tk.Frame(b, bg="#f8f9fa")
    c.pack(side=tk.RIGHT)
    
    def btn(p, t, bg, h, fg, cmd, bold=False):
        x = tk.Label(
                p, text=t, 
                font=("TkDefaultFont", 9, "bold" if bold else "normal"),
                bg=bg, fg=fg, padx=12, pady=3, cursor="hand2")
        x.pack(side=tk.LEFT, padx=(0, 6))
        x.bind("<Enter>", lambda e: x.config(bg=h))
        x.bind("<Leave>", lambda e: x.config(bg=bg))
        x.bind("<Button-1>", lambda e: cmd())
        return x
    
    def no(): 
        confirmed[0] = False
        d.destroy()
        root.destroy()
    
    def yes(): 
        confirmed[0] = True
        d.destroy()
        root.destroy()
    
    btn(c, "Anuluj", "#e9ecef", "#dee2e6", "#212529", no)
    btn(c, "Potwierdzam", "#0d6efd", "#0b5ed7", "white", yes, bold=True).focus_set()  # noqa
    
    m = tk.Frame(f, bg="#f8f9fa")
    m.pack(fill=tk.BOTH, expand=True)
    tk.Label(
        m, text="📝", 
        font=("TkDefaultFont", 24), 
        bg="#f8f9fa").pack(side=tk.LEFT, padx=(0, 8))
    t = tk.Frame(m, bg="#f8f9fa")
    t.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tk.Label(
        t, text="Potwierdź zapisanie zmian w pliku", 
        font=("TkDefaultFont", 10, "bold"),
        bg="#f8f9fa", fg="#212529", wraplength=400,
        anchor=tk.CENTER, justify=tk.CENTER).pack(expand=True)
    
    d.protocol("WM_DELETE_WINDOW", no)
    d.bind('<Return>', lambda e: yes())
    d.bind('<Escape>', lambda e: no())
    d.update_idletasks()
    d.grab_set()
    d.focus_set()
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
