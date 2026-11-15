"""Seller tool: build the antivirus-friendly copy of Fleet Manager Pro that buyers get.

    python "Build Pro Buyer Copy (no exe).py"                                  stock copy (not traceable to a buyer)
    python "Build Pro Buyer Copy (no exe).py" --buyer FMP-XXXX-XXXX-XXXX-XXXX  copy for one purchase (hidden buyer ID)
    (or just double-click  Build Buyer Copy.bat , which asks for everything)

Instead of packing everything into one .exe (which antivirus programs often mistake for malware), this copy ships:
  runtime\\   the official Python, copied from this PC (python.exe and its DLLs are signed by the Python Software
              Foundation) with only the standard library and the packages Fleet Manager Pro needs
  app\\       Fleet Manager Pro compiled to native machine code with Nuitka (fmpro.*.pyd: no Python
              source or bytecode to recover), licence address set to the public one
  Start Fleet Manager Pro.bat / (LAN).bat / Driver Agent.bat
Nothing needs installing on the buyer's PC. Output: ..\\Build Output\\For Buyer (no exe)\\ and its .zip.

Needs once:  python -m pip install nuitka   and Visual Studio (or its Build Tools) with C++.
"""
import compileall
import os
import re
import shutil
import subprocess  # nosec B404  # only runs Nuitka on our own files
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "Fleet Manager Pro")
PARENT = os.path.join(os.path.dirname(HERE), "Build Output")   # ...\RC 1.3 Alpha\Build Output
os.makedirs(PARENT, exist_ok=True)
OUT = os.path.join(PARENT, "For Buyer (no exe)")
NAME = "Fleet Manager Pro"
PUBLIC_LICENCE_SERVER = "https://fleetmanager.servehttp.com:8750"
PY = os.path.dirname(os.path.realpath(sys.executable))
if not os.path.isfile(os.path.join(PY, "python.exe")) or "WindowsApps" in PY:
    PY = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Python", f"pythoncore-{sys.version_info[0]}.{sys.version_info[1]}-64")
# the packages Fleet Manager Pro imports, and what they need (folder names in site-packages)
PACKAGES = ["flask", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker", "waitress",
            "cryptography", "cffi", "pycparser", "colorama"]
STDLIB_SKIP = {"site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
               "__pycache__", "venv", "pydoc_data"}


def copy_runtime(dst):
    os.makedirs(dst)
    for f in os.listdir(PY):
        full = os.path.join(PY, f)
        if os.path.isfile(full) and (f.endswith((".exe", ".dll")) or f == "LICENSE.txt"):
            shutil.copy2(full, dst)
    shutil.copytree(os.path.join(PY, "DLLs"), os.path.join(dst, "DLLs"),
                    ignore=shutil.ignore_patterns("_tkinter*", "tcl*", "tk*", "_test*", "*.pdb"))
    lib = os.path.join(PY, "Lib")
    shutil.copytree(lib, os.path.join(dst, "Lib"),
                    ignore=lambda d, names: [n for n in names if n in STDLIB_SKIP or n.endswith(".pdb")]
                    if os.path.normcase(d) == os.path.normcase(lib) else [n for n in names if n == "__pycache__"])
    sp_src, sp_dst = os.path.join(lib, "site-packages"), os.path.join(dst, "Lib", "site-packages")
    os.makedirs(sp_dst)
    names = os.listdir(sp_src)
    for pkg in PACKAGES:
        found = False
        for n in names:
            low = n.lower()
            if low == pkg or (low.startswith(pkg + "-") and low.endswith(".dist-info")):
                s = os.path.join(sp_src, n)
                (shutil.copytree(s, os.path.join(sp_dst, n), ignore=shutil.ignore_patterns("__pycache__"))
                 if os.path.isdir(s) else shutil.copy2(s, sp_dst))
                found = True
        if not found and pkg != "colorama":
            sys.exit(f"Package '{pkg}' isn't installed in {sp_src}")
    for n in names:                                        # cffi's compiled backend sits loose in site-packages
        if n.startswith("_cffi_backend") and n.endswith(".pyd"):
            shutil.copy2(os.path.join(sp_src, n), sp_dst)


BUYER_RX = re.compile(r"^FMP-[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")
HOST_KEY = os.path.join(HERE, "Fleet Manager License Host", "host_key.json")


def buyer_from_args():
    """--buyer FMP-XXXX-XXXX-XXXX-XXXX builds a copy for one purchase (hidden buyer ID); without it a stock copy."""
    import argparse
    ap = argparse.ArgumentParser(description="Build the buyer copy of Fleet Manager Pro")
    ap.add_argument("--buyer", default="", help="the purchase key this copy is for (License Manager > 2)")
    key = ap.parse_args().buyer.strip().upper()
    if key and not BUYER_RX.match(key):
        sys.exit(f"--buyer must be a purchase key like FMP-XXXX-XXXX-XXXX-XXXX (got {key})")
    return key or None


def seal_pages_for(fmpro_dir, buyer):
    """Seal the pages to this build (only the licence server can unlock them) and return its hidden build tag."""
    sys.path.insert(0, os.path.join(SRC, "tools"))
    import seal_pages
    if not os.path.isfile(HOST_KEY):
        sys.exit(f"Can't find the licence server's key ({HOST_KEY}) - it's needed to seal the pages")
    public, master = seal_pages.master_from_hostkey(HOST_KEY)
    if f'PUBLIC_KEY_HEX = "{public}"' not in open(os.path.join(SRC, "fmpro", "licensing.py"), encoding="utf-8").read():
        sys.exit("host_key.json isn't the key of the licence server this copy trusts - the pages can't be sealed")
    tag = seal_pages.build_tag(master, buyer or "STOCK")
    seal_pages.seal_templates(fmpro_dir, master, tag)
    return tag


def record_build(buyer, tag, kind, folder):
    """Keep a private list of which copy went to whom (Development folder: Buyer Builds (PRIVATE).csv)."""
    import csv
    import datetime
    path = os.path.join(HERE, "Buyer Builds (PRIVATE).csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["built", "purchase key", "hidden build tag", "version", "folder"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), buyer, tag, kind, folder])


def build_app(dst):
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data", "tests", "tools", ".claude",
                                                            "Start Fleet Manager Pro*.bat", "requirements.txt"))
    lic = os.path.join(dst, "fmpro", "licensing.py")
    s = open(lic, encoding="utf-8").read()
    s, n = re.subn(r'SERVER_URL = "[^"]*"', f'SERVER_URL = "{PUBLIC_LICENCE_SERVER}"', s)
    s, m = re.subn(r'LAN_FALLBACK = "[^"]*"', 'LAN_FALLBACK = ""', s)
    if n != 1 or m != 1:
        sys.exit("Couldn't set the licence address in licensing.py")
    # buyer copies can never be switched to a test licence server / signing key (not even by adding a tests folder)
    s, k = re.subn(r"^_DEV = .*$", "_DEV = False", s, flags=re.M)
    if k != 1:
        sys.exit("Couldn't switch off developer mode in licensing.py")
    open(lic, "w", encoding="utf-8", newline="\n").write(s)
    # compile the whole fmpro package to one native module (fmpro.cp3xx-win_amd64.pyd) with Nuitka
    global TAG
    TAG = seal_pages_for(os.path.join(dst, "fmpro"), BUYER)
    nk = dst + "_nuitka"
    cmd = [sys.executable, "-m", "nuitka", "--mode=module", "fmpro", "--python-flag=no_docstrings", "--include-package=fmpro", "--msvc=latest",
           "--assume-yes-for-downloads", "--remove-output", "--output-dir=" + nk]
    r = subprocess.run(cmd, cwd=dst, capture_output=True, text=True)  # nosec B603  # fixed argument list, no shell
    pyd = [f for f in os.listdir(nk) if f.startswith("fmpro.") and f.endswith(".pyd")] if os.path.isdir(nk) else []
    if r.returncode != 0 or len(pyd) != 1:
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit("Nuitka failed (needs: python -m pip install nuitka, and Visual Studio's C++ build tools)")
    shutil.move(os.path.join(nk, pyd[0]), os.path.join(dst, pyd[0]))
    shutil.rmtree(nk, ignore_errors=True)
    # the fmpro folder keeps only its data (styles, cargo list): every .py goes, and the pages are compiled in
    shutil.rmtree(os.path.join(dst, "fmpro", "templates"))
    for r_, _d, fs in os.walk(os.path.join(dst, "fmpro")):
        for f in fs:
            if f.endswith((".py", ".pyc", ".pyi")):
                os.remove(os.path.join(r_, f))
    for r_, _d, _f in sorted(os.walk(os.path.join(dst, "fmpro")), key=lambda t: -len(t[0])):
        if not os.listdir(r_):
            os.rmdir(r_)
    # run.py is only a 3-line stub (the launcher itself is compiled into fmpro): ship it as a .pyc
    if not compileall.compile_file(os.path.join(dst, "run.py"), quiet=1, legacy=True, optimize=1):
        sys.exit("Compiling run.py failed")
    for r_, _d, fs in os.walk(dst):
        for f in fs:
            if f.endswith((".py", ".pyi")):
                os.remove(os.path.join(r_, f))


BAT = '''@echo off
title Fleet Manager Pro{title}
cd /d "%~dp0"
"%~dp0runtime\\python.exe" -E -s -B "%~dp0app\\run.pyc" {args}%*
pause
'''


BUYER = TAG = None


def main():
    global OUT, BUYER
    BUYER = buyer_from_args()
    if BUYER:
        OUT = os.path.join(PARENT, f"For Buyer (no exe) - {BUYER}")
    if not os.path.isfile(os.path.join(PY, "python.exe")):
        sys.exit(f"Couldn't find the Python installation (looked in {PY})")
    work = tempfile.mkdtemp(prefix="fmp-noexe-")
    out = os.path.join(work, NAME)
    print("Copying the signed Python runtime from", PY)
    copy_runtime(os.path.join(out, "runtime"))
    print("Compiling Fleet Manager Pro...")
    build_app(os.path.join(out, "app"))
    leaked = [f for _d, _s, fs in os.walk(os.path.join(out, "app")) for f in fs
              if f.endswith(".py") or (f.endswith(".pyc") and f != "run.pyc")]
    if leaked:
        sys.exit(f"Readable source left in the build: {leaked[:5]}")
    for fname, title, args in (("Start Fleet Manager Pro.bat", "", ""), ("Start Fleet Manager Pro (LAN).bat", " - LAN", "--lan "),
                               ("Driver Agent.bat", " - driver agent", "agent ")):
        with open(os.path.join(out, fname), "w", newline="\r\n") as f:
            f.write(BAT.format(title=title, args=args))
    shutil.copytree(os.path.join(SRC, "plugin"), os.path.join(out, "plugin"), dirs_exist_ok=True)
    shutil.copy2(os.path.join(SRC, "fmpro", "EULA.txt"), os.path.join(out, "Licence Agreement.txt"))
    if not os.path.isfile(os.path.join(out, "app", "fmpro", "EULA.txt")):
        sys.exit("The licence agreement didn't make it into the build")
    guide = os.path.join(HERE, "Guides", "Fleet Manager Pro - Installation Guide.pdf")
    if os.path.exists(guide):
        shutil.copy2(guide, os.path.join(out, "Installation Guide.pdf"))
    version = open(os.path.join(SRC, "VERSION"), encoding="utf-8").read().strip()
    with open(os.path.join(out, "READ ME FIRST.txt"), "w", encoding="utf-8") as f:
        f.write(f"""Fleet Manager Pro {version}
==========================
(c) 2026 Jeff Poblocki. All rights reserved. Use is subject to  Licence Agreement.txt  (you agree to it when you
activate). Copying, sharing, reselling or reverse engineering this software is not allowed.

1. Unzip this folder anywhere you like (for example your Desktop or Documents). Keep everything together.
2. Double-click  Start Fleet Manager Pro.bat . Your browser opens http://127.0.0.1:8760 - nothing to install.
3. Type your purchase key (FMP-...) on the Activate screen, then create your company and owner account.
4. Settings > Telemetry > Game plugin > Install, then start the game (click OK on the "advanced SDK features" note).

If Windows says "Windows protected your PC": More info > Run anyway. If Microsoft Defender flags or removes a file, it
is a false positive while the program isn't code-signed yet - a temporary step until Microsoft signs and clears it.
Restore and allow it in Windows Security > Virus & threat protection > Protection history (Installation Guide, section 1).

Start Fleet Manager Pro (LAN).bat  lets drivers on your network connect (they use the https:// address it prints).
Driver Agent.bat --server https://HOST-PC:8760 --token TOKEN --pin FINGERPRINT   streams a driver's own game.

Your company data is kept in  app\\data  - back that folder up.
The runtime folder is the official Python (Python Software Foundation); see runtime\\LICENSE.txt.
Full instructions: Installation Guide.pdf
""")
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(out, OUT)
    zip_path = OUT + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(OUT):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join(NAME, os.path.relpath(full, OUT)))
    shutil.rmtree(work, ignore_errors=True)
    n = sum(len(fs) for _d, _s, fs in os.walk(OUT))
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(OUT) for f in fs)
    print(f"Done: {OUT}  ({n} files, {size / 1e6:.0f} MB)\n      {zip_path}  ({os.path.getsize(zip_path) / 1e6:.1f} MB)")
    print(f"Built for: {BUYER or 'nobody in particular (stock copy)'}   hidden build tag: {TAG}")
    if BUYER:
        record_build(BUYER, TAG, "standard (no exe)", zip_path)


if __name__ == "__main__":
    main()
