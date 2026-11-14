"""Seller tool: build the copy of Fleet Manager Pro that buyers get.

    python "Build Pro Buyer Copy.py"                                  stock copy (not traceable to a buyer)
    python "Build Pro Buyer Copy.py" --buyer FMP-XXXX-XXXX-XXXX-XXXX  copy for one purchase (hidden buyer ID)
    (or just double-click  Build Buyer Copy.bat , which asks for everything)

Makes  ..\\Build Output\\For Buyer\\  (a compiled program: Fleet Manager Pro.exe, no Python source, no Python needed on
the buyer's PC) and  ..\\Build Output\\For Buyer.zip  (what you send) from the development copy in  Fleet Manager Pro\\ .
The buyer copy checks licences at the public address below and is pinned to this licence server's certificate
and signing key (taken from the development copy's fmpro\\licensing.py).

Compiled to native machine code with Nuitka (no Python source or bytecode to recover).
Needs once:  python -m pip install nuitka   and Visual Studio (or its Build Tools) with C++.
"""
import os
import re
import shutil
import subprocess  # nosec B404  # only runs Nuitka on our own files
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "Fleet Manager Pro")
DOWNLOADS = os.path.join(os.path.dirname(HERE), "Build Output")   # ...\RC 1.3\Build Output
os.makedirs(DOWNLOADS, exist_ok=True)
OUT = os.path.join(DOWNLOADS, "For Buyer")
PUBLIC_LICENCE_SERVER = "https://fleetmanager.servehttp.com:8750"
NAME = "Fleet Manager Pro"


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


def main():
    global OUT
    buyer = buyer_from_args()
    if buyer:
        OUT = os.path.join(DOWNLOADS, f"For Buyer - {buyer}")
    version = open(os.path.join(SRC, "VERSION"), encoding="utf-8").read().strip()
    work = tempfile.mkdtemp(prefix="fmp-build-")
    stage = os.path.join(work, "stage")
    shutil.copytree(SRC, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data", "tests", "tools", ".claude"))

    lic = os.path.join(stage, "fmpro", "licensing.py")
    s = open(lic, encoding="utf-8").read()
    s, n = re.subn(r'SERVER_URL = "[^"]*"', f'SERVER_URL = "{PUBLIC_LICENCE_SERVER}"', s)
    if n != 1:
        sys.exit("Couldn't set SERVER_URL in licensing.py")
    s, n = re.subn(r'LAN_FALLBACK = "[^"]*"', 'LAN_FALLBACK = ""', s)      # buyers never use the seller's home address
    # buyer copies can never be switched to a test licence server / signing key (not even by adding a tests folder)
    s, k = re.subn(r"^_DEV = .*$", "_DEV = False", s, flags=re.M)
    if k != 1:
        sys.exit("Couldn't switch off developer mode in licensing.py")
    open(lic, "w", encoding="utf-8", newline="\n").write(s)

    tag = seal_pages_for(os.path.join(stage, "fmpro"), buyer)
    cmd = [sys.executable, "-m", "nuitka", "--mode=standalone", "--python-flag=no_docstrings", "--msvc=latest", "--assume-yes-for-downloads",
           "--remove-output", "--output-dir=" + os.path.join(work, "dist"), f"--output-filename={NAME}.exe",
           "--windows-console-mode=force", "--include-package=fmpro", "--include-package=waitress",
           "--include-package=cryptography.hazmat.primitives.ciphers",
           "--include-data-dir=" + os.path.join(stage, "fmpro", "static") + "=fmpro/static",
           "--include-data-files=" + os.path.join(stage, "fmpro", "cargo_list.json") + "=fmpro/cargo_list.json",
           "--include-data-files=" + os.path.join(stage, "fmpro", "EULA.txt") + "=fmpro/EULA.txt",
           "--include-data-files=" + os.path.join(stage, "VERSION") + "=VERSION",
           f"--product-name={NAME}", f"--file-version={version}.0.0" if version.count(".") == 1 else f"--file-version={version}",
           f"--product-version={version}.0.0" if version.count(".") == 1 else f"--product-version={version}",
           f"--file-description={NAME}",
           os.path.join(stage, "run.py")]
    print("Compiling with Nuitka (this takes a few minutes)...")
    r = subprocess.run(cmd, cwd=stage, capture_output=True, text=True)  # nosec B603  # fixed argument list, no shell
    built = os.path.join(work, "dist", "run.dist")
    if r.returncode != 0 or not os.path.isfile(os.path.join(built, f"{NAME}.exe")):
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit("Nuitka failed (needs: python -m pip install nuitka, and Visual Studio's C++ build tools)")

    leaked = [os.path.join(d, f) for d, _s, fs in os.walk(built) for f in fs
              if f.endswith((".py", ".pyc")) and ("fmpro" in d or f.startswith(("run.", "licensing.", "pages.")))]
    if leaked:
        sys.exit(f"Source files ended up in the build: {leaked}")
    if os.path.isdir(os.path.join(built, "fmpro", "templates")):
        sys.exit("The page templates ended up as loose files in the build")

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(built, OUT)
    shutil.copytree(os.path.join(stage, "plugin"), os.path.join(OUT, "plugin"))
    shutil.copy2(os.path.join(stage, "fmpro", "EULA.txt"), os.path.join(OUT, "Licence Agreement.txt"))
    if not os.path.isfile(os.path.join(OUT, "fmpro", "EULA.txt")):
        sys.exit("The licence agreement didn't make it into the build")
    with open(os.path.join(OUT, "Start Fleet Manager Pro (LAN).bat"), "w", newline="\r\n") as f:
        f.write('@echo off\ntitle Fleet Manager Pro - LAN\ncd /d "%~dp0"\n"Fleet Manager Pro.exe" --lan %*\npause\n')

    readme = open(os.path.join(stage, "README.md"), encoding="utf-8").read()
    readme = re.sub(r"\n## Tests\n.*?(?=\n## )", "\n", readme, flags=re.S)
    readme = re.sub(r"\n## Project layout\n.*?(?=\n## )", "\n", readme, flags=re.S)
    readme = re.sub(r"\n## Start\n.*?(?=\n## )", '''
## Start

1. Unzip the folder anywhere (for example your Desktop).
2. Double-click **Fleet Manager Pro.exe**. Your browser opens <http://127.0.0.1:8760>. Nothing else needs installing.
   (Windows may show "Windows protected your PC" the first time: click *More info* → *Run anyway*.)
   **Microsoft Defender flagged or removed it?** That's a false positive while the program isn't code-signed yet - a
   temporary step until Microsoft signs and clears it. Restore and allow it in Windows Security → Virus & threat
   protection → Protection history; the Installation Guide (section 1) shows every click.
3. Type your purchase key on the **Activate** screen, then create your company and owner account.
4. **Settings → Telemetry → Game plugin → Install** (or copy `plugin\\\\scs-telemetry.dll` into
   `<game>\\\\bin\\\\win_x64\\\\plugins\\\\`). Start the game and click OK on the "advanced SDK features" message.

To let drivers on your network connect, start it with **Start Fleet Manager Pro (LAN).bat**; they open the
`https://` address it prints. Your data lives in the `data` folder next to the program: back that folder up.

Remote drivers stream their own game with (token and fingerprint are on their profile page):

    "Fleet Manager Pro.exe" agent --server https://HOST-PC:8760 --token TOKEN --pin FINGERPRINT
''', readme, flags=re.S)
    open(os.path.join(OUT, "README.md"), "w", encoding="utf-8", newline="\n").write(readme)
    guide = os.path.join(HERE, "Guides", "Fleet Manager Pro - Installation Guide.pdf")
    if os.path.exists(guide):
        shutil.copy2(guide, os.path.join(OUT, "Installation Guide.pdf"))
    else:
        print("NOTE: no installation guide found - run  python tools\\make_install_guide.py  in Fleet Manager Pro first")

    for old in os.listdir(HERE):
        if old.startswith("Fleet Manager Pro ") and old.endswith(".zip"):
            os.remove(os.path.join(HERE, old))
    zip_path = OUT + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(OUT):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join(NAME, os.path.relpath(full, OUT)))
    shutil.rmtree(work, ignore_errors=True)
    n = sum(len(fs) for _d, _s, fs in os.walk(OUT))
    print(f"Done: {OUT}  ({n} files)\n      {zip_path}  ({os.path.getsize(zip_path) / 1e6:.1f} MB)")
    print(f"Built for: {buyer or 'nobody in particular (stock copy)'}   hidden build tag: {tag}")
    if buyer:
        record_build(buyer, tag, ".exe", zip_path)


if __name__ == "__main__":
    main()
