#!/usr/bin/env python3
# pip install python-pkcs11 pymupdf Pillow

import io
import requests
import base64
import os
import time
import pkcs11
import argparse
import platform
import tomllib
import traceback
import threading
import sys
import tkinter as tk
from pathlib import Path
from pkcs11 import Attribute, Mechanism
from pkcs11.exceptions import PinIncorrect, PinLenRange, PinLocked, PKCS11Error
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa
from cryptography.hazmat.primitives import hashes
from requests.exceptions import RequestException
from tkinter import messagebox
from datetime import datetime

import fitz            # PyMuPDF
from PIL import Image  # Pillow

from addin_paths import log_file


def write_log(message):
    with open(log_file, 'a') as f:
        f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n")
        f.write(message + '\n')


def handle_error(t, v, tb):
    write_log(''.join(traceback.format_exception(t, v, tb)))
    messagebox.showerror('DodatekEZD Error', f"{t.__name__}\n\n{v}")


tk.Tk.report_callback_exception = handle_error
sys.excepthook = handle_error

threading.excepthook = lambda args: handle_error(
    args.exc_type, args.exc_value, args.exc_traceback)


# ============= Smart Card Configuration =============

PIN = None  # Must be passed via argument or get_pin() in handler.py

_CONFIG_PATH = Path(__file__).parent / "smart_card_config.toml"

with open(_CONFIG_PATH, "rb") as f:
    _cfg = tomllib.load(f)

BASE = _cfg["base_url"]
LABEL_PATTERNS = _cfg["smartcard"]["label_patterns"]
LIBRARIES = _cfg["libraries"].get(platform.system(), [])

# ============= PKCS#11 / Smart Card Functions =============


def detect_pkcs11_library_and_label():
    """Find library and matching token label"""
    for library_path in LIBRARIES:
        try:
            lib = pkcs11.lib(library_path)
            for slot in lib.get_slots():
                try:
                    t = slot.get_token()
                    token_label = t.label.strip()
                    if any(token_label.startswith(
                            pattern) for pattern in LABEL_PATTERNS):
                        print(f"✓ Found token: '{token_label}' in:")
                        print(f"  {library_path}")
                        return library_path, token_label
                except pkcs11.TokenNotPresent:
                    continue
        except Exception as e:
            print(f"✗ Failed to load: {library_path} - {e}")
            continue

    print("✗ No matching tokens found")
    return None, None


def open_pkcs11_session(pkcs11_lib: str, token_label: str, pin: str):
    """Open PKCS#11 session and return session, private_key, cert_der, token"""
    lib = pkcs11.lib(pkcs11_lib)

    # Find token
    token = None
    for slot in lib.get_slots():
        try:
            t = slot.get_token()
        except pkcs11.TokenNotPresent:
            continue

        if t.label.strip() == token_label:
            token = t
            break

    if token is None:
        messagebox.showerror(
            'DodatekEZD', f'Token "{token_label}" nie został znaleziony.')
        sys.exit(1)

    if not pin or len(pin) < 6:
        messagebox.showerror(
            'DodatekEZD', 'PIN jest za krótki (minimum 6 znaków).')
        sys.exit(2)

    try:
        session = token.open(user_pin=pin)
    except PinLenRange:
        messagebox.showerror('DodatekEZD', 'PIN jest za krótki lub za długi.')
        sys.exit(2)
    except PinIncorrect:
        messagebox.showerror('DodatekEZD', 'Nieprawidłowy PIN.')
        sys.exit(3)
    except PinLocked:
        messagebox.showerror('DodatekEZD', 'PIN został zablokowany.')
        sys.exit(4)
    except PKCS11Error as e:
        messagebox.showerror('DodatekEZD', f'Błąd PKCS#11: {e}')
        sys.exit(5)
    except Exception as e:
        messagebox.showerror('DodatekEZD', f'Nieoczekiwany błąd: {e}')
        sys.exit(1)

    # Find private key and certificate
    priv_keys = list(session.get_objects({
        Attribute.CLASS: pkcs11.ObjectClass.PRIVATE_KEY,
        Attribute.SIGN: True,
    }))

    if not priv_keys:
        messagebox.showerror(
            'DodatekEZD', 'Brak klucza prywatnego do podpisywania na tokenie.')
        sys.exit(5)

    private = priv_keys[0]

    certs = list(session.get_objects({
        Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE,
    }))

    if not certs:
        messagebox.showerror('DodatekEZD', 'Brak certyfikatu na tokenie.')
        sys.exit(6)

    cert_obj = certs[0]
    cert_der = cert_obj[Attribute.VALUE]

    return session, private, cert_der, token


def build_digest_info(data: bytes) -> bytes:
    """Build DigestInfo structure for SHA-256 (PKCS#1 v1.5).

    Manually constructs the DER-encoded DigestInfo that PKCS#1 v1.5 requires
    when using raw RSA_PKCS (the token does the RSA primitive only; hashing
    and framing are our responsibility).
    """
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    hash_value = digest.finalize()
    assert len(hash_value) == 32, "SHA-256 must produce exactly 32 bytes"

    # DER encoding of AlgorithmIdentifier for SHA-256 + the hash itself:
    # SEQUENCE { SEQUENCE { OID 2.16.840.1.101.3.4.2.1, NULL } OCTET STRING }
    sha256_digestinfo_prefix = bytes.fromhex(
        "3031300d060960864801650304020105000420"
    )
    return sha256_digestinfo_prefix + hash_value


def detect_signing_mechanism(token, private_key, cert_der):
    """Detect best available signing mechanism based on key type"""
    mechanisms = token.slot.get_mechanisms()

    # Determine key type from certificate
    cert = x509.load_der_x509_certificate(cert_der, default_backend())
    public_key = cert.public_key()

    # ECDSA key
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        if Mechanism.ECDSA_SHA256 in mechanisms:
            return Mechanism.ECDSA_SHA256
        elif Mechanism.ECDSA_SHA384 in mechanisms:
            return Mechanism.ECDSA_SHA384
        elif Mechanism.ECDSA_SHA512 in mechanisms:
            return Mechanism.ECDSA_SHA512
        else:
            raise RuntimeError("No compatible ECDSA signing mechanism found")

    # RSA key
    elif isinstance(public_key, rsa.RSAPublicKey):
        if Mechanism.SHA256_RSA_PKCS in mechanisms:
            return Mechanism.SHA256_RSA_PKCS
        elif Mechanism.SHA384_RSA_PKCS in mechanisms:
            return Mechanism.SHA384_RSA_PKCS
        elif Mechanism.SHA512_RSA_PKCS in mechanisms:
            return Mechanism.SHA512_RSA_PKCS
        elif Mechanism.RSA_PKCS in mechanisms:
            return Mechanism.RSA_PKCS
        else:
            raise RuntimeError("No compatible RSA signing mechanism found")

    else:
        raise RuntimeError(
            f"Unsupported key type: {type(public_key).__name__}")


def sign_with_smartcard(session, private_key, token, cert_der, data: bytes):
    """Sign data with smart card"""
    mechanism = detect_signing_mechanism(token, private_key, cert_der)
    print(f"  Smart card mechanism: {mechanism}")

    # RSA_PKCS: token performs raw RSA only — we must hash and frame manually
    if mechanism == Mechanism.RSA_PKCS:
        digest_info = build_digest_info(data)
        signature_raw = private_key.sign(
            digest_info, mechanism=Mechanism.RSA_PKCS)
        print(f"  DigestInfo size: {len(digest_info)} bytes")
        print(f"  Signature: {len(signature_raw)} bytes")
        return signature_raw

    signature_raw = private_key.sign(data, mechanism=mechanism)
    print(f"  Raw signature: {len(signature_raw)} bytes")

    # Convert ECDSA signature from P1363 to DER format
    cert = x509.load_der_x509_certificate(cert_der, default_backend())
    public_key = cert.public_key()

    if isinstance(public_key, ec.EllipticCurvePublicKey):
        # P1363 format: r || s (two equal parts)
        sig_len = len(signature_raw)
        r = int.from_bytes(signature_raw[:sig_len // 2], 'big')
        s = int.from_bytes(signature_raw[sig_len // 2:], 'big')

        # Convert to DER format
        signature_der = encode_dss_signature(r, s)
        print(f"  Converted P1363 → DER: {len(signature_der)} bytes")
        return signature_der

    # RSA with high-level mechanism: signature already in correct format
    return signature_raw

# ============= DSS Signing Functions =============


def detect_key_type(cert):
    """Detect if certificate uses RSA or ECDSA"""
    public_key = cert.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        return "RSA", "RSA_SHA256"
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        return "ECDSA", "ECDSA_SHA256"
    else:
        raise ValueError("Unsupported key type")


def get_output_filename(path, signature_level, packaging):
    """Generate output filename based on signature format"""

    # Extract directory, filename, and extension
    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    base_name, ext = os.path.splitext(filename)

    # Determine the output filename based on signature format
    if signature_level.startswith('PAdES'):
        output_filename = f"{base_name}.pdf"
    elif signature_level.startswith('XAdES'):
        if packaging == "ENVELOPED":
            output_filename = f"{base_name}.xml"
        elif packaging == "ENVELOPING":
            output_filename = f"{base_name}{ext}.xml"
        elif packaging == "DETACHED":
            output_filename = f"{base_name}{ext}.xades"
        else:
            output_filename = f"{base_name}.xml"
    else:
        output_filename = f"{base_name}.xml"

    return os.path.join(directory, output_filename)


# ============= PAdES Visual Signature Placement =============


# Minimum stamp size in PDF points — fits 4 lines at def. 12pt font + padding
MIN_W_PT = 200
MIN_H_PT = 70


def pick_signature_rect(pdf_path: str, page_index: int = 0):
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    # Compute SCALE so the tallest/widest page fits within the screen.
    # A hidden root is created early solely to read screen dimensions.
    win = tk.Tk()
    win.withdraw()
    _UI_OVERHEAD = 160  # title bar + nav row + status label + buttons + padding
    _max_pt_h = max(doc[i].rect.height for i in range(total_pages))
    _max_pt_w = max(doc[i].rect.width  for i in range(total_pages))
    SCALE = min(1.5,
                (win.winfo_screenheight() - _UI_OVERHEAD) / _max_pt_h,
                (win.winfo_screenwidth()  - 32)           / _max_pt_w)

    # Pre-render all pages as PhotoImage-ready PNG bytes
    page_pixmaps = []
    for i in range(total_pages):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
        page_pixmaps.append(pix)
    doc.close()

    # Use the largest page dimensions for the canvas
    canvas_w = max(p.width for p in page_pixmaps)
    canvas_h = max(p.height for p in page_pixmaps)

    result = [None]
    rect_id = [None]
    start = [0, 0]
    current_page = [max(0, min(page_index, total_pages - 1))]

    def px_to_dss(x0, y0, x1, y1):
        f = 1.0 / SCALE
        return (
            round(x0 * f, 1),
            round(y0 * f, 1),
            round((x1 - x0) * f, 1),
            round((y1 - y0) * f, 1),
        )

    def enforce_minimum(x0, y0, x1, y1):
        """Expand rect to minimum stamp size if drawn too small."""
        pix = page_pixmaps[current_page[0]]
        min_w_px = MIN_W_PT * SCALE
        min_h_px = MIN_H_PT * SCALE
        if x1 - x0 < min_w_px:
            x1 = x0 + min_w_px
        if y1 - y0 < min_h_px:
            y1 = y0 + min_h_px
        # clamp to current page bounds
        x1 = min(x1, pix.width)
        y1 = min(y1, pix.height)
        return x0, y0, x1, y1

    # --- Window ---
    win.title("Wskaż miejsce wizualizacji podpisu i potwierdź")
    win.deiconify()
    win.resizable(False, False)

    # Cache PhotoImage objects (must be kept alive to avoid GC)
    photos = []
    for pix in page_pixmaps:
        data = base64.b64encode(pix.tobytes("png")).decode()
        photos.append(tk.PhotoImage(data=data))

    canvas = tk.Canvas(win, width=canvas_w, height=canvas_h,
                       cursor="crosshair")
    canvas.pack(padx=8, pady=8)
    img_item = canvas.create_image(
        0, 0, anchor="nw", image=photos[current_page[0]])

    def refresh_page():
        """
        Redraw the canvas with the current page image; clear any drawn rect.
        """
        canvas.itemconfig(img_item, image=photos[current_page[0]])
        pix = page_pixmaps[current_page[0]]
        canvas.config(width=pix.width, height=pix.height)
        if rect_id[0]:
            canvas.delete(rect_id[0])
            rect_id[0] = None
        btn_ok.config(state="disabled")
        page_label.set(
            f"Strona {current_page[0] + 1} / {total_pages}")
        status.set(f"Narysuj prostokąt (min {MIN_W_PT}×{MIN_H_PT} pt)")

    # --- Navigation bar ---
    nav_row = tk.Frame(win)
    nav_row.pack(pady=(0, 2))

    def go_prev():
        if current_page[0] > 0:
            current_page[0] -= 1
            refresh_page()

    def go_next():
        if current_page[0] < total_pages - 1:
            current_page[0] += 1
            refresh_page()

    page_label = tk.StringVar(
        value=f"Strona {current_page[0] + 1} / {total_pages}")
    tk.Button(nav_row, text="◀ Poprzednia", width=14,
              command=go_prev).pack(side="left", padx=4)
    tk.Label(nav_row, textvariable=page_label,
             font=("Helvetica", 10, "bold"), width=16).pack(side="left")
    tk.Button(nav_row, text="Następna ▶", width=14,
              command=go_next).pack(side="left", padx=4)

    status = tk.StringVar(
        value=f"Narysuj prostokąt (min {MIN_W_PT}×{MIN_H_PT} pt)")
    tk.Label(
        win, textvariable=status, font=("Helvetica", 10)).pack(pady=(0, 4))

    btn_row = tk.Frame(win)
    btn_row.pack(pady=(0, 8))
    btn_ok = tk.Button(btn_row, text="Potwierdź", width=12, state="disabled")
    btn_no = tk.Button(btn_row, text="Anuluj",  width=12, command=win.destroy)
    btn_ok.pack(side="left", padx=4)
    btn_no.pack(side="left", padx=4)

    # --- Mouse handlers ---
    def draw_rect(x0, y0, x1, y1):
        if rect_id[0]:
            canvas.delete(rect_id[0])
        rect_id[0] = canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="#0055FF", width=2,
            fill="#0055FF", stipple="gray25",
        )

    def on_press(e):
        start[0], start[1] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])
            rect_id[0] = None
        btn_ok.config(state="disabled")

    def on_drag(e):
        x0, y0 = start[0], start[1]
        x1, y1 = max(e.x, x0 + 1), max(e.y, y0 + 1)
        draw_rect(x0, y0, x1, y1)
        ox, oy, w, h = px_to_dss(x0, y0, x1, y1)
        status.set(f"x={ox} pt   y={oy} pt   w={w} pt   h={h} pt")

    def on_release(e):
        x0, y0 = start[0], start[1]
        x1, y1 = max(e.x, x0 + 1), max(e.y, y0 + 1)

        x0, y0, x1, y1 = enforce_minimum(x0, y0, x1, y1)
        draw_rect(x0, y0, x1, y1)

        ox, oy, w, h = px_to_dss(x0, y0, x1, y1)
        confirmed_page = current_page[0]

        # Check whether the drawn rectangle overlaps any existing text
        check_doc = fitz.open(pdf_path)
        clip = fitz.Rect(ox, oy, ox + w, oy + h)
        has_text = bool(check_doc[confirmed_page].get_text(
            "text", clip=clip).strip())
        check_doc.close()

        if has_text:
            status.set("Nie można zakrywać istniejącego tekstu!")
            btn_ok.config(state="disabled")
        else:
            status.set("") 
            
            def confirm():
                result[0] = (ox, oy, w, h, confirmed_page + 1)
                win.destroy()

            btn_ok.config(state="normal", command=confirm)

    canvas.bind("<ButtonPress-1>",   on_press)
    canvas.bind("<B1-Motion>",       on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    win.mainloop()
    return result[0]


def build_pades_image_parameters(
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
    page: int,
    logo_path: str = None,
) -> dict:
    """
    Build the imageParameters dict for the DSS REST API PAdES visual appearance

    Args:
        origin_x:     X offset from page left in mm (DSS bottom-left origin).
        origin_y:     Y offset from page bottom in mm (DSS bottom-left origin).
        width:        Stamp width in mm.
        height:       Stamp height in mm.
        page:         1-based page number.
        logo_path:    Optional PNG/JPEG logo shown on the left side.

    Returns:
        dict ready to set as parameters["imageParameters"] in sign_file().
    """
    stamp_text = "\n".join([
        "Podpisano elektronicznie przez:",
        "   {CN}",     # resolved from cert at sign time
        "Data i czas podpisu:",
        "   {DATE}",   # resolved from signing time
    ])

    text_params = {
        "text": stamp_text,
    }
    if logo_path:
        text_params["signerTextPosition"] = "LEFT"
    
    image_params = {
        "fieldParameters": {
            "page":    page,
            "originX": origin_x,
            "originY": origin_y,
            "width":   width,
            "height":  height,
        },
        "textParameters": text_params,
        "backgroundColor": {"red": 255, "green": 255, "blue": 255},
        "zoom": 100,
    }

    if logo_path:
        image_params["image"] = _encode_logo(logo_path)

    return image_params


def _encode_logo(logo_path: str) -> dict:
    """Load a logo image, flatten alpha, and return DSS image dict."""
    with Image.open(logo_path) as img:
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return {
            "bytes":     base64.b64encode(buf.getvalue()).decode(),
            "mediaType": "image/png",
        }


def sign_file(path, signature_level="XAdES_BASELINE_B",
              packaging="ENVELOPING", pin=None,
              image_parameters: dict = None):
    """
    Sign document with DSS REST API using smart card.

    Args:
        path:             Document path.
        signature_level:  PAdES_BASELINE_B, XAdES_BASELINE_B, CAdES_BASELINE_B.
        packaging:        ENVELOPED, ENVELOPING, DETACHED (XAdES only).
        pin:              Smart card PIN (uses global PIN if None).
        image_parameters: Optional DSS imageParameters dict for PAdES visual
                          stamp. Build with build_pades_image_parameters() or
                        pick_signature_rect() + build_pades_image_parameters()
                          Ignored for non-PAdES formats.
    """
    # Find and open smart card
    pkcs11_lib, token_label = detect_pkcs11_library_and_label()
    if not pkcs11_lib:
        raise RuntimeError("No smart card found")

    used_pin = pin if pin else PIN
    session, private_key, cert_der, token = open_pkcs11_session(
        pkcs11_lib, token_label, used_pin)

    try:
        # Load certificate
        cert = x509.load_der_x509_certificate(cert_der, default_backend())
        key_type, encryption_algorithm = detect_key_type(cert)

        # Prepare document
        with open(path, 'rb') as f:
            file_b64 = base64.b64encode(f.read()).decode()
        cert_b64 = base64.b64encode(cert_der).decode()

        # Validate packaging for PAdES
        if signature_level.startswith('PAdES'):
            packaging = "ENVELOPED"

        print(f"\n{'='*60}")
        print(f"Signing: {os.path.basename(path)}")
        print(f"Format: {signature_level}, Packaging: {packaging}")
        print(f"Key Type: {key_type}")
        print(f"{'='*60}")

        # Signing date in milliseconds (MUST be same for both calls)
        signing_date_ms = int(time.time() * 1000)

        # Build parameters
        parameters = {
            "signingCertificate": {"encodedCertificate": cert_b64},
            "signatureLevel": signature_level,
            "signaturePackaging": packaging,
            "digestAlgorithm": "SHA256",
            "encryptionAlgorithm": key_type,
            "blevelParams": {
                "trustAnchorBPPolicy": True,
                "signingDate": signing_date_ms
            }
        }

        # Attach visual stamp for PAdES when provided
        if image_parameters and signature_level.startswith("PAdES"):
            cn = cert.subject.get_attributes_for_oid(
                x509.NameOID.COMMON_NAME)[0].value
            signing_date_str = datetime.fromtimestamp(
                signing_date_ms / 1000).strftime("%Y.%m.%d %H:%M:%S")
            image_parameters["textParameters"]["text"] = (
                image_parameters["textParameters"]["text"]
                .replace("{CN}", cn)
                .replace("{DATE}", signing_date_str)
            )
            parameters["imageParameters"] = image_parameters
            print("  Visual stamp: enabled")

        payload = {
            "parameters": parameters,
            "toSignDocument": {
                "bytes": file_b64,
                "name": os.path.basename(path)
            }
        }

        # Step 1: Get data to sign
        print("[1/3] Getting data to sign from DSS...")
        try:
            r1 = requests.post(
                f"{BASE}/signature/one-document/getDataToSign",
                json=payload,
                timeout=30
            )
            r1.raise_for_status()
        except RequestException as e:
            raise RuntimeError(
                f"Cannot reach DSS service (getDataToSign): {e}") from e

        to_be_signed = base64.b64decode(r1.json()['bytes'])
        print(f"  Received: {len(to_be_signed)} bytes")

        # Step 2: Sign with smart card
        print("[2/3] Signing with smart card...")
        signature = sign_with_smartcard(session, private_key, token,
                                        cert_der, to_be_signed)
        print(f"  Signature: {len(signature)} bytes")

        # Step 3: Complete signature with DSS
        print("[3/3] Completing signature with DSS...")
        payload['signatureValue'] = {
            "algorithm": encryption_algorithm,
            "value": base64.b64encode(signature).decode()
        }

        try:
            r2 = requests.post(
                f"{BASE}/signature/one-document/signDocument",
                json=payload,
                timeout=30
            )
            r2.raise_for_status()
        except RequestException as e:
            raise RuntimeError(
                f"Cannot reach DSS service (signDocument): {e}") from e

        signed_bytes = base64.b64decode(r2.json()['bytes'])
        output_path = get_output_filename(path, signature_level, packaging)

        with open(output_path, 'wb') as f:
            f.write(signed_bytes)

        print(f"\n✓ Saved: {os.path.basename(output_path)}")
        print(f"  Size: {len(signed_bytes)} bytes")
        return output_path

    finally:
        session.close()


# ============= Main Usage Examples =============


def cli():
    parser = argparse.ArgumentParser(
        description="Sign a document with DSS and a smart card"
    )
    parser.add_argument(
        "file",
        help="Path to file to sign"
    )
    parser.add_argument(
        "--type",
        choices=["xades", "pades"],
        default="xades",
        help="Signature type: xades or pades (default: xades)"
    )
    parser.add_argument(
        "--packaging",
        choices=["ENVELOPED", "ENVELOPING", "DETACHED"],
        default="ENVELOPING",
        help="Signature packaging (for XAdES): ENVELOPED, ENVELOPING or DETACHED "  # noqa
             "(default: ENVELOPING). For PAdES this is always ENVELOPED."
    )
    parser.add_argument(
        "--level",
        choices=["B", "T"],
        default="B",
        help="Baseline level suffix"
    )
    parser.add_argument(
        "--pin",
        help="Smart card PIN (if omitted, uses PIN constant from script)"
    )
    parser.add_argument(
        "--visual",
        action="store_true",
        help="Show PDF preview and let user draw the visual stamp rectangle "
             "(PAdES only)"
    )
    parser.add_argument(
        "--page",
        type=int,
        default=0,
        help="0-based page index for the visual stamp picker (default: 0)"
    )

    args = parser.parse_args()

    # Map CLI type/level to DSS signature_level
    if args.type == "pades":
        signature_level = f"PAdES_BASELINE_{args.level}"
        packaging = "ENVELOPED"  # PAdES must be enveloped
    else:
        signature_level = f"XAdES_BASELINE_{args.level}"
        packaging = args.packaging

    # Build visual stamp parameters when requested (PAdES only)
    image_parameters = None
    if args.visual and signature_level.startswith("PAdES"):
        coords = pick_signature_rect(args.file, page_index=args.page)
        if coords is None:
            print("Signing cancelled.")
            sys.exit(0)

        origin_x, origin_y, width, height, page = coords
        image_parameters = build_pades_image_parameters(
            origin_x=origin_x,
            origin_y=origin_y,
            width=width,
            height=height,
            page=page,
        )

    output_path = sign_file(
        args.file,
        signature_level=signature_level,
        packaging=packaging,
        pin=args.pin,
        image_parameters=image_parameters,
    )
    print(f"\nOutput file: {output_path}")


if __name__ == "__main__":
    cli()
