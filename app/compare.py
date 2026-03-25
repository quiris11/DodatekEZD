#!/usr/bin/env python3
"""
compare.py — Universal document & spreadsheet diff viewer
Supported formats:
  Documents   : .txt .docx .odt .rtf .doc (and any format LibreOffice can open)
  Spreadsheets: .xlsx .xls .ods .csv

Usage:
  python compare.py file1 file2
  python compare.py file1 file2 -o report.html

Requirements (install what you need):
  pip install python-docx odfpy striprtf pandas openpyxl xlrd
  LibreOffice must be installed for .doc and other legacy formats.
"""

import os
import sys
import html as html_lib
import argparse
import difflib
import subprocess
import tempfile
import webbrowser

# ── format groups ────────────────────────────────────────────────────────────

SPREADSHEET_EXTS = {".xlsx", ".xls", ".ods", ".csv"}
DOCUMENT_EXTS = {".txt", ".docx", ".odt", ".rtf", ".doc"}


def file_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in SPREADSHEET_EXTS:
        return "spreadsheet"
    return "document"   # includes LibreOffice fallback for unknown exts


# ── document readers ─────────────────────────────────────────────────────────

def read_txt(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [line.strip() for line in f if line.strip()]


def read_docx(path):
    try:
        from docx import Document
        from docx.oxml.ns import qn
    except ImportError:
        sys.exit("python-docx not installed.  Run: pip install python-docx")

    doc = Document(path)
    lines = []

    def _para_text(para_elem):
        """
        Extract visible text from a paragraph XML element.

        Covers:
        - Regular text runs (w:r / w:t)
        - Legacy FORMTEXT  : value lives in a w:t run between fldChar
                             separate…end — captured via the plain-text path
        - Legacy FORMCHECKBOX : state lives in w:ffData/w:checkBox/w:checked
                             — no w:t is ever written; emitted as [x] / [ ]
        - Legacy FORMDROPDOWN : selection lives in w:ffData/w:ddList/w:result
                             — no w:t is ever written; emitted as the option text
        - Inline content controls (w:sdt inside w:p): iter() reaches their
                             w:r children automatically
        """
        parts = []
        for r in para_elem.iter(qn('w:r')):

            # ── legacy form-field: checkbox / dropdown ────────────────────
            fld_char = r.find(qn('w:fldChar'))
            if fld_char is not None:
                ff_data = fld_char.find(qn('w:ffData'))
                if ff_data is not None:
                    checkbox = ff_data.find(qn('w:checkBox'))
                    ddlist = ff_data.find(qn('w:ddList'))
                    if checkbox is not None:
                        checked = checkbox.find(qn('w:checked'))
                        val = (checked.get(qn('w:val'), '1')
                               if checked is not None else '0')
                        parts.append('[x]' if val not in (
                            '0', 'false') else '[ ]')
                    elif ddlist is not None:
                        result_e = ddlist.find(qn('w:result'))
                        entries = ddlist.findall(qn('w:listEntry'))
                        try:
                            idx = int(result_e.get(qn('w:val'), 0)
                                      if result_e is not None else 0)
                        except (ValueError, TypeError):
                            idx = 0
                        if entries and idx < len(entries):
                            parts.append(entries[idx].get(qn('w:val'), ''))
                # fldChar runs never carry a w:t — skip to next run
                continue

            # ── skip field-instruction runs (e.g. " FORMTEXT ") ──────────
            if r.find(qn('w:instrText')) is not None:
                continue

            # ── plain text (including FORMTEXT current-value runs) ────────
            for t_elem in r.iter(qn('w:t')):
                parts.append(t_elem.text or '')

        return ''.join(parts)

    def _walk(elem):
        """
        Walk a body / sdtContent / table-cell element in document order,
        appending non-empty paragraph texts to *lines*.

        Handles:
        - w:p  — paragraphs (passed to _para_text)
        - w:sdt — block-level content controls; recurse into w:sdtContent
                  (doc.paragraphs never returns paragraphs nested inside sdt)
        - w:tbl — tables; recurse into each cell so table text is preserved
                  (doc.paragraphs skips table-cell paragraphs entirely)
        """
        for child in elem:
            tag = child.tag
            if tag == qn('w:p'):
                text = _para_text(child)
                if text.strip():
                    lines.append(text)
            elif tag == qn('w:sdt'):
                content = child.find(qn('w:sdtContent'))
                if content is not None:
                    _walk(content)
            elif tag == qn('w:tbl'):
                for tr in child:
                    if tr.tag == qn('w:tr'):
                        for tc in tr:
                            if tc.tag == qn('w:tc'):
                                _walk(tc)

    _walk(doc.element.body)
    return lines


def read_odt(path):
    """
    Read ODT paragraphs, including text inside inline wrappers such as
    text:span, text:a (hyperlinks), and text:text-input form fields.

    Limitation: ODT checkbox / radio-button controls live in the
    <office:forms> section outside the text flow and are not extracted here.
    """
    try:
        from odf.opendocument import load
        from odf.text import P
    except ImportError:
        sys.exit("odfpy not installed.  Run: pip install odfpy")

    def _elem_text(node):
        """Recursively collect all text content from an odfpy element."""
        parts = []
        if node.nodeType == node.TEXT_NODE:
            parts.append(node.data)
        for child in node.childNodes:
            parts.append(_elem_text(child))
        return ''.join(parts)

    doc = load(path)
    return [t for p in doc.getElementsByType(P)
            for t in [_elem_text(p)] if t.strip()]


def read_rtf(path):
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:
        sys.exit("striprtf not installed.  Run: pip install striprtf")
    with open(path, encoding="utf-8", errors="replace") as f:
        text = rtf_to_text(f.read())
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_via_libreoffice(path):
    """Fallback: convert to .txt via LibreOffice headless."""
    tmp_dir = tempfile.mkdtemp()
    try:
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "txt:Text",
             "--outdir", tmp_dir, path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        sys.exit(
            "LibreOffice not found. Install it to handle .doc and other "
            "legacy formats.")
    base = os.path.splitext(os.path.basename(path))[0]
    return read_txt(os.path.join(tmp_dir, base + ".txt"))


def read_document(path):
    ext = os.path.splitext(path)[1].lower()
    readers = {
        ".txt":  read_txt,
        ".docx": read_docx,
        ".odt":  read_odt,
        ".rtf":  read_rtf,
    }
    reader = readers.get(ext)
    if reader:
        return reader(path)
    print(f"  Using LibreOffice to read {ext} …")
    return read_via_libreoffice(path)


# ── spreadsheet reader ───────────────────────────────────────────────────────

def read_spreadsheet(path):
    try:
        import pandas as pd
    except ImportError:
        sys.exit("pandas not installed.  Run: pip install pandas")

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    engines = {"xlsx": "openpyxl", "xls": "xlrd", "ods": "odf"}
    engine = engines.get(ext)
    if engine is None:
        sys.exit(f"Unsupported spreadsheet format: .{ext}")

    try:
        sheets = pd.read_excel(path, sheet_name=None, engine=engine, dtype=str)
    except ImportError:
        hints = {"openpyxl": "pip install openpyxl",
                 "xlrd":     "pip install xlrd",
                 "odf":      "pip install odfpy"}
        sys.exit(f"Missing library for .{ext}: {hints.get(engine, '')}")

    result = {}
    for name, df in sheets.items():
        df = df.fillna("")
        header = " | ".join(str(c) for c in df.columns)
        rows = [header] + [" | ".join(
            str(v) for v in row) for row in df.values.tolist()]
        result[name] = rows
    return result


# ── unified reader ───────────────────────────────────────────────────────────
# Both readers return the same shape:  dict[section_name -> list[str]]
# For documents there is one section named "Document".

def read_file(path):
    if file_type(path) == "spreadsheet":
        return read_spreadsheet(path), "spreadsheet"
    else:
        lines = read_document(path)
        return {"Document": lines}, "document"


# ── diff engine ──────────────────────────────────────────────────────────────

def word_level_diff(a, b):
    aw, bw = a.split(), b.split()
    matcher = difflib.SequenceMatcher(None, aw, bw)
    parts = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            parts.append(html_lib.escape(" ".join(aw[i1:i2])))
        elif op == "replace":
            parts.append(f'<span class="removed">{html_lib.escape(
                " ".join(aw[i1:i2]))}</span>')
            parts.append(f'<span class="added">{html_lib.escape(
                " ".join(bw[j1:j2]))}</span>')
        elif op == "delete":
            parts.append(f'<span class="removed">{html_lib.escape(
                " ".join(aw[i1:i2]))}</span>')
        elif op == "insert":
            parts.append(f'<span class="added">{html_lib.escape(
                " ".join(bw[j1:j2]))}</span>')
    return " ".join(parts)


def diff_section(rows1, rows2):
    matcher = difflib.SequenceMatcher(None, rows1, rows2)
    paras = []
    added = deleted = changed = 0

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for line in rows1[i1:i2]:
                paras.append(f'<p class="para unchanged">{html_lib.escape(
                    line)}</p>')
        elif op == "replace":
            pairs = list(zip(rows1[i1:i2], rows2[j1:j2]))
            for a, b in pairs:
                changed += 1
                paras.append(
                    f'<p class="para modified" data-type="modified">'
                    f'<span class="pill pill-mod">Zmodyfikowane</span>'
                    f'{word_level_diff(a, b)}</p>')
            for k in range(len(pairs), i2 - i1):
                deleted += 1
                paras.append(
                    f'<p class="para deleted" data-type="deleted">'
                    f'<span class="pill pill-del">Usunięte</span>'
                    f'{html_lib.escape(rows1[i1 + k])}</p>')
            for k in range(len(pairs), j2 - j1):
                added += 1
                paras.append(
                    f'<p class="para inserted" data-type="inserted">'
                    f'<span class="pill pill-add">Dodane</span>'
                    f'{html_lib.escape(rows2[j1 + k])}</p>')
        elif op == "delete":
            for line in rows1[i1:i2]:
                deleted += 1
                paras.append(
                    f'<p class="para deleted" data-type="deleted">'
                    f'<span class="pill pill-del">Usunięte</span>'
                    f'{html_lib.escape(line)}</p>')
        elif op == "insert":
            for line in rows2[j1:j2]:
                added += 1
                paras.append(
                    f'<p class="para inserted" data-type="inserted">'
                    f'<span class="pill pill-add">Dodane</span>'
                    f'{html_lib.escape(line)}</p>')

    return paras, added, deleted, changed


def build_sections(data1, data2):
    all_keys = list(dict.fromkeys(list(data1.keys()) + list(data2.keys())))
    sections = []
    total_added = total_deleted = total_changed = 0

    for key in all_keys:
        rows1 = data1.get(key, [])
        rows2 = data2.get(key, [])
        status = "new" if key not in data1 else (
            "removed" if key not in data2 else "both")
        paras, added, deleted, changed = diff_section(rows1, rows2)
        total_added += added
        total_deleted += deleted
        total_changed += changed
        sections.append((key, status, paras, added, deleted, changed))

    return sections, total_added, total_deleted, total_changed


# ── HTML renderer ────────────────────────────────────────────────────────────

SHEET_ICON = """
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" 
  style="vertical-align:middle;margin-right:6px">
  <rect x="1" y="1" width="12" height="12" rx="2" stroke="#888" stroke-width="1.2" fill="none"/>
  <line x1="1" y1="5" x2="13" y2="5" stroke="#888" stroke-width="1"/>
  <line x1="1" y1="9" x2="13" y2="9" stroke="#888" stroke-width="1"/>
  <line x1="5" y1="1" x2="5" y2="13" stroke="#888" stroke-width="1"/>
</svg>"""

DOC_ICON = """
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none"
  style="vertical-align:middle;margin-right:6px">
  <rect x="2" y="1" width="10" height="12" rx="1.5" stroke="#888" stroke-width="1.2" fill="none"/>
  <line x1="4" y1="4.5" x2="10" y2="4.5" stroke="#888" stroke-width="1"/>
  <line x1="4" y1="7"   x2="10" y2="7"   stroke="#888" stroke-width="1"/>
  <line x1="4" y1="9.5" x2="8"  y2="9.5" stroke="#888" stroke-width="1"/>
</svg>"""


def render_html(path1, path2, kind, sections, total_added, total_deleted, 
                total_changed):
    f1 = os.path.basename(path1)
    f2 = os.path.basename(path2)
    ext1 = os.path.splitext(f1)[1].upper().lstrip(".")
    ext2 = os.path.splitext(f2)[1].upper().lstrip(".")
    total = total_added + total_deleted + total_changed
    icon = SHEET_ICON if kind == "spreadsheet" else DOC_ICON
    mono = "font-mono" if kind == "spreadsheet" else ""

    section_html = []
    for key, status, paras, added, deleted, changed in sections:
        badge = ""
        if status == "new":
            badge = '<span class="sheet-badge badge-new">Nowe</span>'
        elif status == "removed":
            badge = '<span class="sheet-badge badge-del">Usunięte</span>'

        content = "".join(
            paras) if paras else '<p class="no-changes">No content.</p>'
        sub = (
            f"Dodane: {added}  &nbsp; Usunięte: {deleted} &nbsp; Zmodyfkowane: {changed} "
            if (added or deleted or changed) else "Brak zmian")

        # For documents with a single "Document" section, skip the header
        show_header = not (kind == "document" and key == "Document")
        header_html = f"""
          <div class="sheet-header">
            <div class="sheet-title">{icon}{html_lib.escape(key)} {badge}</div>
            <div class="sheet-sub">{sub}</div>
          </div>""" if show_header else ""

        section_html.append(f"""
        <div class="sheet-section">
          {header_html}
          <div class="sheet-body {mono}">{content}</div>
        </div>""")

    return f"""
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>Porównanie: {f1} vs {f2}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #f5f4f0;
    font-family: Georgia, 'Times New Roman', serif;
    color: #1a1a1a; min-height: 100vh; padding: 2rem 1rem;
  }}
  .toolbar {{
    max-width: 1100px; margin: 0 auto 1.5rem;
    background: #fff; border: 1px solid #ddd; border-radius: 10px;
    padding: .75rem 1.25rem;
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: .75rem;
  }}
  .file-info {{ font-family: sans-serif; font-size: 13px; color: #555; }}
  .file-info strong {{ color: #111; }}
  .ext-badge {{
    display: inline-block; font-family: sans-serif; font-size: 10px;
    font-weight: 700; padding: 1px 6px; border-radius: 4px;
    margin-left: 4px; vertical-align: middle; background: #eee; color: #555;
  }}
  .arrow {{ color: #aaa; margin: 0 6px; }}
  .stats {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .stat {{
    font-family: sans-serif; font-size: 12px; font-weight: 600;
    padding: 3px 11px; border-radius: 20px; cursor: pointer;
    border: 1.5px solid transparent; transition: opacity .15s;
  }}
  .stat.inactive {{ opacity: .35; }}
  .stat-all {{ background: #eef2ff; color: #3a4dbf; border-color: #b3bcf5; }}
  .stat-add {{ background: #e6f9ee; color: #1a7a3a; border-color: #9fe0bb; }}
  .stat-del {{ background: #fdecea; color: #c0392b; border-color: #f5b7b1; }}
  .stat-mod {{ background: #fff8e1; color: #9a6800; border-color: #ffe082; }}
  .nav-btns {{ display: flex; gap: 6px; }}
  .nav-btns button {{
    font-family: sans-serif; font-size: 12px;
    background: #f0eeea; border: 1px solid #ccc; border-radius: 6px;
    padding: 4px 12px; cursor: pointer; color: #333;
  }}
  .nav-btns button:hover {{ background: #e4e2de; }}
  .document {{
    max-width: 1100px; margin: 0 auto;
    background: #fff; border: 1px solid #ddd; border-radius: 10px;
    overflow: hidden;
  }}
  .sheet-section {{ border-bottom: 1px solid #e8e6e1; }}
  .sheet-section:last-child {{ border-bottom: none; }}
  .sheet-header {{
    padding: .75rem 1.5rem; background: #fafaf8;
    border-bottom: 1px solid #eee;
    display: flex; align-items: baseline;
    justify-content: space-between; flex-wrap: wrap; gap: .5rem;
  }}
  .sheet-title {{
    font-family: sans-serif; font-size: 13px; font-weight: 700;
    color: #333; display: flex; align-items: center;
  }}
  .sheet-sub  {{ font-family: sans-serif; font-size: 11px; color: #999; }}
  .sheet-badge {{
    font-family: sans-serif; font-size: 10px; font-weight: 700;
    padding: 2px 8px; border-radius: 20px; margin-left: 8px; vertical-align: middle;
  }}
  .badge-new {{ background: #e6f9ee; color: #1a7a3a; }}
  .badge-del {{ background: #fdecea; color: #c0392b; }}
  .sheet-body {{ padding: 1.5rem 2rem; line-height: 1.85; }}
  .font-mono  {{ font-family: 'Fira Mono', 'Cascadia Code', Consolas, monospace; }}
  .para {{
    position: relative; margin-bottom: .7em;
    padding: .3em .6em .3em 1em;
    border-left: 3px solid transparent;
    border-radius: 0 5px 5px 0; font-size: 14px;
  }}
  .unchanged {{ border-left-color: transparent; }}
  .inserted  {{ background: #f0fdf5; border-left-color: #34c270; }}
  .deleted   {{ background: #fff5f5; border-left-color: #e05252;
                text-decoration: line-through; color: #999; }}
  .modified  {{ background: #fffbee; border-left-color: #f0c040; }}
  .inserted:hover {{ background: #e2faed; }}
  .deleted:hover  {{ background: #ffe8e8; }}
  .modified:hover {{ background: #fff5cc; }}
  span.added   {{ background: #c8f7dc; color: #145a2c;
                  border-radius: 3px; padding: 0 3px; }}
  span.removed {{ background: #fdd; color: #922;
                  border-radius: 3px; padding: 0 3px; text-decoration: line-through; }}
  .pill {{
    display: inline-block; font-family: sans-serif;
    font-size: 10px; font-weight: 700; letter-spacing: .04em;
    text-transform: uppercase; padding: 2px 8px; border-radius: 20px;
    margin-right: 8px; vertical-align: middle; position: relative; top: -1px;
  }}
  .pill-add {{ background: #d4f5e4; color: #1a7a3a; }}
  .pill-del {{ background: #fdd;    color: #922; }}
  .pill-mod {{ background: #fff0b3; color: #7a5800; }}
  .no-changes {{ font-family: sans-serif; color: #aaa; font-size: 13px; padding: .5rem 0; }}
  .highlight-nav {{ outline: 3px solid #3a4dbf; outline-offset: 2px; border-radius: 5px; }}
  @media (max-width: 600px) {{ .sheet-body {{ padding: 1rem; }} }}
</style>
</head>
<body>

<div class="toolbar">
  <div class="file-info">
    <strong>{f1}</strong><span class="ext-badge">{ext1}</span>
    <span class="arrow">&#8594;</span>
    <strong>{f2}</strong><span class="ext-badge">{ext2}</span>
  </div>
  <div class="stats">
    <span class="stat stat-all" onclick="filter('all')">Wszystkie &nbsp;{total}</span>
    <span class="stat stat-add" onclick="filter('inserted')">Dodane &nbsp;{total_added}</span>
    <span class="stat stat-del" onclick="filter('deleted')">Usunięte &nbsp;{total_deleted}</span>
    <span class="stat stat-mod" onclick="filter('modified')">Zmodyfikowane &nbsp;{total_changed}</span>
  </div>
  <div class="nav-btns">
    <button onclick="jump(-1)">&#8593; Poprzednie</button>
    <button onclick="jump(1)">&#8595; Następne</button>
  </div>
</div>

<div class="document">
  {"".join(section_html)}
</div>

<script>
  let activeFilters = new Set(['inserted','deleted','modified']);
  let navIndex = -1;

  function filter(type) {{
    if (type === 'all') {{
      activeFilters = activeFilters.size === 3
        ? new Set()
        : new Set(['inserted','deleted','modified']);
    }} else {{
      activeFilters.has(type) ? activeFilters.delete(type) : activeFilters.add(type);
    }}
    document.querySelectorAll('[data-type]').forEach(p => {{
      p.style.display = activeFilters.has(p.dataset.type) ? '' : 'none';
    }});
    const map = {{inserted:'add', deleted:'del', modified:'mod'}};
    document.querySelectorAll('.stat').forEach(s => {{
      s.classList.toggle('inactive',
        (s.classList.contains('stat-all') && activeFilters.size < 3) ||
        Object.entries(map).some(([k,v]) =>
          s.classList.contains('stat-'+v) && !activeFilters.has(k))
      );
    }});
    navIndex = -1;
  }}

  function visibleChanges() {{
    return Array.from(document.querySelectorAll('[data-type]'))
      .filter(p => p.style.display !== 'none');
  }}

  function jump(dir) {{
    const items = visibleChanges();
    if (!items.length) return;
    items.forEach(p => p.classList.remove('highlight-nav'));
    navIndex = (navIndex + dir + items.length) % items.length;
    items[navIndex].classList.add('highlight-nav');
    items[navIndex].scrollIntoView({{behavior:'smooth', block:'center'}});
  }}

  document.addEventListener('keydown', e => {{
    if (e.key === 'ArrowDown' || e.key === 'n') jump(1);
    if (e.key === 'ArrowUp'   || e.key === 'p') jump(-1);
  }});
</script>
</body>
</html>"""


# ── entry point ──────────────────────────────────────────────────────────────

def compare(path1, path2):
    t1 = file_type(path1)
    t2 = file_type(path2)

    # Allow mixing doc + spreadsheet only if both sides are the same kind
    # (cross-kind comparison is nonsensical)
    if t1 != t2:
        sys.exit(
            f"Cannot compare a {t1} with a {t2}.\n"
            "Both files must be documents or both must be spreadsheets."
        )

    kind = t1
    print(f"Mode: {kind}")
    print(f"Reading: {path1}")
    data1, _ = read_file(path1)
    print(f"Reading: {path2}")
    data2, _ = read_file(path2)

    sections, total_added, total_deleted, total_changed = build_sections(
        data1, data2)
    html = render_html(path1, path2, kind, sections,
                       total_added, total_deleted, total_changed)

    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".html", mode="w", encoding="utf-8")
    tmp.write(html)
    tmp.close()
    webbrowser.open(f"file://{tmp.name}")
    print(f"Opened: {tmp.name}")
    print(f"Changes: +{total_added} added  -{total_deleted} removed"
          f"  ~{total_changed} modified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Compare two documents or spreadsheets and save a visual HTML diff.\n"  # noqa
            "Documents  : .txt .docx .odt .rtf .doc (and LibreOffice-supported formats)\n"  # noqa
            "Spreadsheets: .xlsx .xls .ods"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file1", help="Original file")
    parser.add_argument("file2", help="Modified file")
    args = parser.parse_args()
    compare(args.file1, args.file2)
