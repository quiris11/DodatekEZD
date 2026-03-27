#!/usr/bin/python3
# pip install pikepdf zeep
import zeep_patch  # noqa: F401  # must be first — fixes zeep binary file bug

import base64
import binascii
from datetime import datetime
import fcntl
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
import traceback
import urllib.parse
import zipfile

from compare import compare
from lxml import etree
import pikepdf
import zeep

from addin_paths import addin_path, downloads_folder, log_file, python_x86
from file_monitor import open_and_monitor


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

LOCK_FILE = '/tmp/dodatek_ezd.lock'
_lock_fh = None


def acquire_single_instance_lock():
    global _lock_fh
    _lock_fh = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
    except OSError:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showinfo(
            'DodatekEZD',
            'DodatekEZD jest już uruchomiony.\nPoczekaj na zakończenie '
            'poprzedniej operacji.'
        )
        root.destroy()
        sys.exit(0)


def release_single_instance_lock():
    global _lock_fh
    if _lock_fh:
        fcntl.flock(_lock_fh, fcntl.LOCK_UN)
        _lock_fh.close()
        _lock_fh = None
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


class CustomAskString(simpledialog._QueryString):
    def body(self, master):
        super().body(master)
        self.bind('<Return>', self.ok)
        self.bind('<KP_Enter>', self.ok)  # Also bind keypad Enter
        return self.entry


def get_pin():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    dialog = CustomAskString(
        "EZD Smart Card PIN",
        "Enter PIN:",
        show='*',
        parent=root
    )
    pin = dialog.result

    root.destroy()
    return pin


def remove_empty_rels_files(docx_path):
    """Remove empty .rels files from DOCX (overwrites original)"""
    removed = []
    backup_path = docx_path + '.bak'

    # Create backup
    shutil.copy2(docx_path, backup_path)

    try:
        with zipfile.ZipFile(backup_path, 'r') as zip_in:
            with zipfile.ZipFile(
                    docx_path, 'w', zipfile.ZIP_DEFLATED) as zip_out:
                for item in zip_in.infolist():
                    if item.filename.endswith('.rels'):
                        content = zip_in.read(item.filename)
                        tree = etree.fromstring(content)

                        ns = {'r': 'http://schemas.openxmlformats.org/package'
                              '/2006/relationships'}
                        relationships = tree.findall('.//r:Relationship', ns)

                        if len(relationships) == 0:
                            print(f"Removing empty: {item.filename}")
                            removed.append(item.filename)
                            continue

                    buffer = zip_in.read(item.filename)
                    zip_out.writestr(item, buffer)

        # Remove backup if successful
        os.remove(backup_path)
        print(f"\n✓ Overwritten! Removed {len(removed)} empty .rels files")

    except Exception as e:
        # Restore from backup if error
        shutil.copy2(backup_path, docx_path)
        os.remove(backup_path)
        raise e

    return removed


def dss_sign(file_path, signature_type='xades', packaging='ENVELOPED',
             level='B', pin=None):
    """Sign a document using PKCS#11 via x86 Python or x64 on Linux"""

    PKCS11_ARM64 = True

    if PKCS11_ARM64:
        from dss_pkcs11_signer import sign_file

        if signature_type == "pades":
            signature_level = f"PAdES_BASELINE_{level}"
            packaging = "ENVELOPED"  # PAdES must be enveloped
        else:
            signature_level = f"XAdES_BASELINE_{level}"

        result = sign_file(
            file_path,
            signature_level,
            packaging,
            pin,
        )

    else:
        # Map CLI type/level to DSS signature_level
        cmd = [os.path.join(python_x86, 'python'),
               os.path.join(addin_path, 'dss_pkcs11_signer.py'),
               '--type', signature_type,
               '--packaging', packaging,
               '--level', level]
        if pin:
            cmd.extend(['--pin', pin])
        cmd.append(file_path)
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Check for errors based on exit code
        if result.returncode != 0:
            error_msg = result.stderr.strip()  # Clean error message of subprcs

            if result.returncode == 2:
                raise ValueError(error_msg)   # "PIN is too short or too long"
            elif result.returncode == 3:
                raise ValueError(error_msg)   # "Wrong PIN provided"
            elif result.returncode == 4:
                raise ValueError(error_msg)   # "PIN is locked"
            elif result.returncode == 5:
                raise RuntimeError(error_msg)  # "PKCS#11 error: ..."
            else:
                raise RuntimeError(error_msg)  # Any other error
    return result


def start_podman_and_container(container_name="test", podman_path=None):
    """
    Check if podman machine is started, start it if not,
    and start specified container.

    Args:
        container_name (str): Name of the container to start. Defaults "test".
        podman_path (str): Full path to podman binary.
            If None, will attempt to find it.

    Returns:
        bool: True if successful, False otherwise.
    """
    # Find podman binary
    if podman_path is None:
        podman_path = shutil.which("podman")
        if podman_path is None:
            # Common macOS locations
            common_paths = [
                "/usr/local/bin/podman",
                "/opt/homebrew/bin/podman",
                "/opt/podman/bin/podman"
            ]
            for path in common_paths:
                if os.path.exists(path):
                    podman_path = path
                    break

        if podman_path is None:
            print("Podman not found. Please specify podman_path or add it to PATH.")  # noqa
            return False

    print(f"Using podman at: {podman_path}")

    def run_command(cmd, capture_output=True):
        """Run a shell command and return the result."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                check=False
            )
            return result
        except Exception as e:
            write_log(f"Error running command: {e}")
            print(f"Error running command: {e}")
            return None

    if system == 'Darwin':
        # Check if machine is running
        result = run_command([podman_path, "machine", "list", "--format", "json"])  # noqa
        machine_running = False

        if result and result.returncode == 0:
            try:
                machines = json.loads(result.stdout)
                for machine in machines:
                    if machine.get("Running", False):
                        machine_running = True
                        print(f"Podman machine '{machine.get('Name', 'unknown')}' is already running.")  # noqa
                        break
            except json.JSONDecodeError:
                pass

        # Start machine if not running
        if not machine_running:
            print("Starting Podman machine...")
            result = run_command([podman_path, "machine", "start"])
            if not result or result.returncode != 0:
                print("Failed to start Podman machine.")
                if result:
                    print(f"Error: {result.stderr}")
                return False
            print("Podman machine started successfully.")
            time.sleep(3)  # Wait for machine to be fully ready

    # Start container
    print(f"Starting container '{container_name}'...")
    result = run_command([podman_path, "start", container_name])
    if result and result.returncode == 0:
        print(f"Container '{container_name}' started successfully.")
        return True
    else:
        print(f"Failed to start container '{container_name}'.")
        if result:
            print(f"Error: {result.stderr}")
        return False


def decode_if_base64(data):
    try:
        decoded = base64.b64decode(data, validate=True)
        return decoded
    except binascii.Error as e:
        print(f"Not valid base64: {e}")
        return data  # Return original if not encoded


def is_data_ok(expected_hash, binary_data):
    source_hash = base64.b64decode(expected_hash).hex()
    dest_hash = hashlib.sha256(binary_data).hexdigest()
    return source_hash == dest_hash


def is_pdf_by_header(filename):
    with open(filename, 'rb') as file:
        header = file.read(5)
    return header == b'%PDF-'


def pades_signature_detected(pdf_path):
    if is_pdf_by_header(pdf_path):
        pdf = pikepdf.Pdf.open(pdf_path)

        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Dictionary):
                # Check for the /Sig type (signature object)
                if "/Type" in obj and obj["/Type"] == "/Sig":
                    # Check for the subfilter to confirm it's a PAdES signature
                    if "/SubFilter" in obj and obj[
                            "/SubFilter"] == "/ETSI.CAdES.detached":
                        print("PAdES signature detected!")
                        return True

    print("No PAdES signature found.")
    return False


def prepare_tmp_path(downloads_folder, tmp_folder_name):
    tmp_path = os.path.join(downloads_folder, tmp_folder_name)
    if not os.path.exists(tmp_path):
        os.makedirs(tmp_path)

    # clear content
    for filename in os.listdir(tmp_path):
        file_path = os.path.join(tmp_path, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
    return tmp_path


def decode_ezd_url(url):
    if url.startswith("ezd://"):
        ezd_url = url[len("ezd://"):]

        parts = ezd_url.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError("Invalid EZD URL format")

        token = parts[0]
        encoded_url = parts[1]
        token2 = parts[2] if len(parts) > 2 else None

        try:
            host = base64.b64decode(encoded_url).decode('utf-8')
        except Exception as e:
            raise ValueError("Invalid Base64 encoding") from e
        return host, token, token2


def handle_url(url):
    parsed_url = urllib.parse.urlparse(url)
    host = base64.b64decode(parsed_url.path.strip('/')).decode('utf-8')
    token = parsed_url.hostname
    return host, token


def get_local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def get_file_modification_time(file_path):
    if os.path.exists(file_path):
        return os.path.getmtime(file_path)
    return None


def is_file_in_use(file_path):
    """Check if file is in use using lsof command"""
    try:
        result = subprocess.run(
            ['lsof', file_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def wait_for_file_to_be_closed(file_path):
    print(f"Waiting for {file_path} to be closed...")
    while is_file_in_use(file_path):
        time.sleep(2)
    print(f"{file_path} has been closed.")


def upload_file(file_path, proxyHost, authToken):
    wsdl = f'{str(proxyHost)}EzdProxy.svc?singleWsdl'

    client = zeep.Client(wsdl=wsdl)
    service = client.bind('EzdProxy', 'BasicHttpBinding_IAddInProxy')

    header_data = {
        'AuthToken': authToken,
        'IPAddress': get_local_ip()
    }

    with open(file_path, "rb") as file:
        file_data = file.read()

    try:
        response = service.AddInUploadFile(
            FileByteStream=file_data,
            _soapheaders=header_data
        )
        response_data = zeep.helpers.serialize_object(response)
        print("Response:", response_data)

    except Exception as e:
        write_log(str(e))
        print("Error:", str(e))


def get_file(authToken, proxyHost):
    wsdl = f'{str(proxyHost)}EzdProxy.svc?singleWsdl'

    client = zeep.Client(wsdl=wsdl)
    service = client.bind('EzdProxy', 'BasicHttpBinding_IAddInProxy')

    header_data = {
        'AuthToken': authToken,
        'IPAddress': get_local_ip()
    }

    fileInfo = service.AddInSprawdzDane(
        _soapheaders=header_data
    )

    response = service.AddInDownloadFile(
        _soapheaders=header_data
    )

    file_data = response['body']['FileByteStream']
    file_name = fileInfo['body']['DokumentNazwa']
    return file_data, file_name


def sign_file(authToken, proxyHost, pin, sig_folder):
    wsdl = f'{str(proxyHost)}EzdProxy.svc?singleWsdl'
    client = zeep.Client(wsdl=wsdl)
    service = client.bind('EzdProxy', 'BasicHttpBinding_IAddInProxy')
    header_data = {
        'AuthToken': authToken,
        'IPAddress': get_local_ip()
    }

    fileInfo = service.AddInSprawdzDane(
        _soapheaders=header_data
    )

    # Add delay to let server close previous connection cleanly
    time.sleep(2)

    response = service.AddInPodpisDane(
        _soapheaders=header_data
    )

    tryb = response['header']['Tryb']
    print(f"Tryb podpisu: {tryb}")
    timeStamp = response['header']['IncludeTimestampFromTSA']

    if tryb == 6:
        # PAdES format
        file_data = response['body']['FileByteStream']
        file_name = fileInfo['body']['DokumentNazwa'] + '.pdf'

        output_file = os.path.join(sig_folder, file_name)

        with open(output_file, "wb") as f:
            f.write(file_data)

        if pades_signature_detected(output_file):
            with open(output_file, 'rb') as file:
                byte_stream = file.read()

            with open(output_file, 'wb') as file:
                file.write(byte_stream)

        if timeStamp:
            dss_sign(output_file, 'pades', 'ENVELOPED', 'T', pin)
        else:
            dss_sign(output_file, 'pades', 'ENVELOPED', 'B', pin)

        with open(output_file, "rb") as file:
            file_data = file.read()

        try:
            response = service.AddInPodpisUpload(
                FileByteStream=file_data,
                _soapheaders=header_data
            )
            response_data = zeep.helpers.serialize_object(response)
            print("Response:", response_data)

        except Exception as e:
            write_log(str(e))
            print("Error:", str(e))

    elif tryb == 4 or tryb == 5:

        # XAdES DETACHED format
        file_content = service.AddInDownloadFile(
            _soapheaders=header_data
        )

        expected_hash = response['header']['FileHash256']
        file_data = file_content['body']['FileByteStream']

        if not is_data_ok(expected_hash, file_data):
            print('Dane do podpisu niepoprawne!')
            sys.exit(1)

        file_name = fileInfo['body']['DokumentNazwa']

        output_file = os.path.join(sig_folder, file_name)

        with open(output_file, "wb") as f:
            f.write(file_data)

        if tryb == 5:
            xades_data = response['body']['FileByteStream']
            xades_file = os.path.join(sig_folder, file_name + '.xades')
            with open(xades_file, "wb") as f:
                f.write(xades_data)
        if tryb == 4:
            if timeStamp:
                dss_sign(output_file, 'xades', 'DETACHED', 'T', pin)
            else:
                dss_sign(output_file, 'xades', 'DETACHED', 'B', pin)

        elif tryb == 5:
            messagebox.showerror(
                'DodatekEZD', 'DSS nie obsługuje wielu podpisów typu XAdES '
                'zewnętrzny (detached)')
            return

        if tryb == 4:
            with open(output_file + '.xades', "rb") as file:
                file_data = file.read()
        elif tryb == 5:
            with open(xades_file, "rb") as file:
                file_data = file.read()

        try:
            response = service.AddInPodpisUpload(
                FileByteStream=file_data,
                _soapheaders=header_data
            )
            response_data = zeep.helpers.serialize_object(response)
            print("Response:", response_data)

        except Exception as e:
            write_log(str(e))
            print("Error:", str(e))

    elif tryb == 1:
        # XAdES ENVELOPED format

        file_data = response['body']['FileByteStream']
        file_name = fileInfo['body']['DokumentNazwa']

        output_file = os.path.join(sig_folder, file_name)

        with open(output_file, "wb") as f:
            f.write(file_data)

        if timeStamp:
            dss_sign(output_file, 'xades', 'ENVELOPED', 'T', pin)
        else:
            dss_sign(output_file, 'xades', 'ENVELOPED', 'B', pin)

        with open(os.path.splitext(
                output_file)[0] + '.xml', "rb") as file:
            file_data = file.read()

        try:
            response = service.AddInPodpisUpload(
                FileByteStream=file_data,
                _soapheaders=header_data
            )
            response_data = zeep.helpers.serialize_object(response)
            print("Response:", response_data)

        except Exception as e:
            write_log(str(e))
            print("Error:", str(e))

    elif tryb == 2 or tryb == 3:
        # XAdES ENVELOPING format
        if tryb == 2:
            if (response['header']['FileName'] == 'notatka' or
                    response['header']['FileName'] == 'opinia'):
                file_name = response['header']['FileName']
            else:
                file_name = fileInfo['body']['DokumentNazwa']
        elif tryb == 3:
            if (response['header']['FileName'] == 'notatka' or
                    response['header']['FileName'] == 'opinia'):
                file_name = response['header']['FileName']
            else:
                file_name = fileInfo['body']['DokumentNazwa'][:-6]
        file_data = response['body']['FileByteStream']

        output_file = os.path.join(sig_folder, file_name)

        with open(output_file, "wb") as f:
            decoded = decode_if_base64(file_data)
            f.write(decoded)

        if tryb == 2:
            if timeStamp:
                dss_sign(output_file, 'xades', 'ENVELOPING', 'T', pin)
            else:
                dss_sign(output_file, 'xades', 'ENVELOPING', 'B', pin)

        elif tryb == 3:
            messagebox.showerror(
                'DodatekEZD', 'DSS nie obsługuje wielu podpisów typu XAdES '
                'otaczający (ENVELOPING)')
            return

        # Fix encoding problem in case of notatka or opinia

        if (response['header']['FileName'] == 'notatka' or
                response['header']['FileName'] == 'opinia'):

            parser = etree.XMLParser(remove_blank_text=False)
            tree = etree.parse(output_file + '.xml', parser)
            root = tree.getroot()

            ns_map = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}

            objects = root.xpath("//ds:Object[@Id]", namespaces=ns_map)

            if objects:
                last_object = objects[-1]  # Get the last with Id

                object_id = last_object.get("Id")
                reference = root.xpath(
                    f"//ds:Reference[@URI='#{object_id}']", namespaces=ns_map)

                if reference:
                    last_object.set(
                        "Encoding", "http://www.w3.org/2000/09/xmldsig#base64")

                    with open(output_file + '.xml', "wb") as f:
                        tree.write(f, xml_declaration=True, encoding="UTF-8")

                    print(f"Encoding attribute added to {object_id}.")

            else:
                print("No matching elements found.")

        with open(output_file + '.xml', "rb") as file:
            file_data = file.read()

        try:
            response = service.AddInPodpisUpload(
                FileByteStream=file_data,
                _soapheaders=header_data
            )
            response_data = zeep.helpers.serialize_object(response)
            print("Response:", response_data)

        except Exception as e:
            write_log(str(e))
            print("Error:", str(e))
    else:
        messagebox.showerror(
            'DodatekEZD',
            f'Nieobsługiwany tryb podpisu: {response}')
        return


if __name__ == "__main__":
    acquire_single_instance_lock()
    try:
        system = platform.system()

        is_dss_started = start_podman_and_container("dss")

        os.makedirs(downloads_folder, exist_ok=True)

        if len(sys.argv) > 1 and is_dss_started:
            host, token, token2 = decode_ezd_url(sys.argv[1])

            if len(sys.argv) > 2:
                PIN = sys.argv[2]
            else:
                PIN = ''
            handler_path = prepare_tmp_path(downloads_folder, 'dodatek_ezd')
            if token2 == 'a12':
                PIN = get_pin()
                sign_file(token, host, PIN, handler_path)
            elif token2 == 'a13':
                raise NotImplementedError(
                    'Brak obsługi definicji podpisu w Ustawieniach '
                    'Pracownika.')
            elif token2 == 'a11':
                raise NotImplementedError(
                    'Brak obsługi drukowania kodów kreskowych.')
            elif token2 is None:
                file_data, file_name = get_file(token, host)
                output_file = os.path.join(
                    handler_path,
                    file_name)
                with open(output_file, "wb") as f:
                    f.write(file_data)
                changed = open_and_monitor(output_file)
                if changed:
                    if output_file.lower().endswith('.docx'):
                        # Fix for broken OOXML files from OnlyOffice
                        removed = remove_empty_rels_files(output_file)
                    upload_file(output_file, host, token)

            else:
                file_data, file_name = get_file(token, host)

                output_file = os.path.join(
                    handler_path, '[OLD] ' + file_name)
                with open(output_file, "wb") as f:
                    f.write(file_data)

                file_data2, file_name2 = get_file(token2, host)
                output_file2 = os.path.join(
                    handler_path, '[NEW] ' + file_name2)
                with open(output_file2, "wb") as f:
                    f.write(file_data2)

                if system == 'Darwin' or system == 'Linux':
                    compare(
                        output_file,
                        output_file2)
                else:
                    raise NotImplementedError(
                        f"System {system} nieobsługiwany.")
    finally:
        release_single_instance_lock()
