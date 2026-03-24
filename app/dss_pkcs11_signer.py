#!/usr/bin/env python3
# pip install python-pkcs11

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
from requests.exceptions import RequestException
from tkinter import messagebox
from datetime import datetime

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
        else:
            raise RuntimeError("No compatible RSA signing mechanism found")
    
    else:
        raise RuntimeError(
            f"Unsupported key type: {type(public_key).__name__}")


def sign_with_smartcard(session, private_key, token, cert_der, data: bytes):
    """Sign data with smart card"""
    mechanism = detect_signing_mechanism(token, private_key, cert_der)
    signature_raw = private_key.sign(data, mechanism=mechanism)
    
    print(f"  Smart card mechanism: {mechanism}")
    print(f"  Raw signature: {len(signature_raw)} bytes")
    
    # Convert ECDSA signature from P1363 to DER format
    cert = x509.load_der_x509_certificate(cert_der, default_backend())
    public_key = cert.public_key()
    
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        # P1363 format: r || s (two equal parts)
        sig_len = len(signature_raw)
        r = int.from_bytes(signature_raw[:sig_len//2], 'big')
        s = int.from_bytes(signature_raw[sig_len//2:], 'big')
        
        # Convert to DER format
        signature_der = encode_dss_signature(r, s)
        print(f"  Converted P1363 → DER: {len(signature_der)} bytes")
        return signature_der
    else:
        # RSA signature is already in correct format
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


def sign_file(path, signature_level="XAdES_BASELINE_B",
              packaging="ENVELOPING", pin=None):
    """
    Sign document with DSS REST API using smart card

    Args:
        path: Document path
        signature_level: PAdES_BASELINE_B, XAdES_BASELINE_B, CAdES_BASELINE_B
        packaging: ENVELOPED, ENVELOPING, DETACHED (XAdES only)
        pin: Smart card PIN (uses global PIN if None)
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

    args = parser.parse_args()

    # Map CLI type/level to DSS signature_level
    if args.type == "pades":
        signature_level = f"PAdES_BASELINE_{args.level}"
        packaging = "ENVELOPED"  # PAdES must be enveloped
    else:
        signature_level = f"XAdES_BASELINE_{args.level}"
        packaging = args.packaging

    output_path = sign_file(
        args.file,
        signature_level=signature_level,
        packaging=packaging,
        pin=args.pin,
    )
    print(f"\nOutput file: {output_path}")


if __name__ == "__main__":
    cli()
