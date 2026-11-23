"""Build the copy of Fleet Manager Pro that everyone downloads (it's free: no licence key, no activation).

    python "Build Public Copy.py"            (or double-click  Build Public Copy.bat )

Makes  ..\\Build Output\\For Everyone\\  (a compiled program: Fleet Manager Pro.exe, no Python source, no Python needed)
and  ..\\Build Output\\For Everyone.zip  from the development copy in  Fleet Manager Pro\\ . The copy carries the public
build tag (its update channel), talks to the Fleet Manager server at the public address below, and is pinned to that
server's certificate and update-signing key (taken from the development copy's fmpro\\licensing.py).
--public is still accepted (it's the only kind of build now).

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
DOWNLOADS = os.path.join(os.path.dirname(HERE), "Build Output")   # ...\RC 1.3 Alpha\Build Output
os.makedirs(DOWNLOADS, exist_ok=True)
OUT = os.path.join(DOWNLOADS, "For Everyone")
PUBLIC_LICENCE_SERVER = "https://fleetmanager.servehttp.com:8750"
NAME = "Fleet Manager Pro"
DATA_README = "READ ME - Your Data.txt"


HOST_KEY = os.path.join(HERE, "Fleet Manager License Host", "host_key.json")


def args():
    import argparse
    ap = argparse.ArgumentParser(description="Build the public copy of Fleet Manager Pro")
    ap.add_argument("--public", action="store_true", help="(the default - kept so older commands still work)")
    return ap.parse_args()


def pages_for(fmpro_dir):
    """Compile the pages into the copy and return its build tag (the public update channel)."""
    sys.path.insert(0, os.path.join(SRC, "tools"))
    import seal_pages
    if not os.path.isfile(HOST_KEY):
        sys.exit(f"Can't find the server's key ({HOST_KEY}) - it's needed for the build tag")
    server_key, master = seal_pages.master_from_hostkey(HOST_KEY)
    if f'PUBLIC_KEY_HEX = "{server_key}"' not in open(os.path.join(SRC, "fmpro", "licensing.py"), encoding="utf-8").read():
        sys.exit("host_key.json isn't the key of the server this copy trusts - its updates couldn't be checked")
    tag = seal_pages.build_tag(master, seal_pages.PUBLIC_BUILD)
    seal_pages.open_templates(fmpro_dir, tag)
    return tag


def tk_library_args(work):
    """Nuitka options that bundle Tcl/Tk for the in-game overlay window. Tcl 9 (Python 3.15+) keeps its script
    library inside its DLL, where Nuitka can't find it, so copy both libraries out to folders and point Nuitka there."""
    import base64
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    libs = {"tcl": r.eval("info library"), "tk": r.eval("set tk_library")}
    if not any(v.startswith("//zipfs:") for v in libs.values()):
        return []                                                     # ordinary folders: Nuitka finds them itself

    def copy(src, dst):
        for f in r.eval(f"glob -nocomplain -directory {{{src}}} *").split():
            target = os.path.join(dst, f.rsplit("/", 1)[-1])
            if r.eval(f"file isdirectory {{{f}}}") == "1":
                copy(f, target)
            else:
                os.makedirs(dst, exist_ok=True)
                data = r.eval(f"set h [open {{{f}}} rb]; set d [read $h]; close $h; binary encode base64 $d")
                with open(target, "wb") as fh:
                    fh.write(base64.b64decode(data))
    args = []
    for name, src in libs.items():
        dst = os.path.join(work, f"{name}_library")
        copy(src, dst)
        args.append(f"--{name}-library-dir={dst}")
    return args


def main():
    args()
    version = open(os.path.join(SRC, "VERSION"), encoding="utf-8").read().strip()
    # Windows file/product version fields must be purely numeric (e.g. 1.3.0.0), so strip any
    # suffix like "-alpha" from VERSION for Nuitka while the readable VERSION file ships as-is.
    numver = ".".join((re.findall(r"\d+", version) + ["0", "0", "0", "0"])[:4])
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

    tag = pages_for(os.path.join(stage, "fmpro"))
    cmd = [sys.executable, "-m", "nuitka", "--mode=standalone", "--python-flag=no_docstrings", "--msvc=latest", "--assume-yes-for-downloads",
           "--remove-output", "--output-dir=" + os.path.join(work, "dist"), f"--output-filename={NAME}.exe",
           "--windows-console-mode=force", "--include-package=fmpro", "--include-package=waitress",
           "--enable-plugin=tk-inter", *tk_library_args(work),           # the in-game overlay window (fmpro/ingame.py)
           "--include-package=cryptography.hazmat.primitives.ciphers",
           "--include-data-dir=" + os.path.join(stage, "fmpro", "static") + "=fmpro/static",
           "--include-data-files=" + os.path.join(stage, "fmpro", "cargo_list.json") + "=fmpro/cargo_list.json",
           "--include-data-files=" + os.path.join(stage, "fmpro", "EULA.txt") + "=fmpro/EULA.txt",
           "--include-data-files=" + os.path.join(stage, "VERSION") + "=VERSION",
           f"--product-name={NAME}", f"--file-version={numver}", f"--product-version={numver}",
           f"--file-description={NAME} - virtual trucking company manager",
           "--company-name=Jeff Poblocki", "--copyright=Copyright (c) 2026 Jeff Poblocki. All rights reserved.",
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

1. Run **Fleet Manager Pro Setup.exe** (or, if you were sent a zip, unzip it anywhere, for example your Desktop).
2. Start **Fleet Manager Pro** from the Start menu (zip: double-click **Fleet Manager Pro.exe**). Your browser opens <http://127.0.0.1:8760>. Nothing else needs installing.
   (Windows may show "Windows protected your PC" the first time: click *More info* → *Run anyway*.)
   **Microsoft Defender flagged or removed it?** That's a false positive while the program isn't code-signed yet - a
   temporary step until Microsoft signs and clears it. Restore and allow it in Windows Security → Virus & threat
   protection → Protection history; the Installation Guide (section 1) shows every click.
3. Read and accept the licence agreement, then create your company and owner account. No key is needed.
4. **Settings → Telemetry → Game plugin → Install** (or copy `plugin\\\\scs-telemetry.dll` into
   `<game>\\\\bin\\\\win_x64\\\\plugins\\\\`). Start the game and click OK on the "advanced SDK features" message.

To let drivers connect, open **Drivers > Connect a driver** and press **Let other PCs in** (once); they open the
`https://` address it shows. (Starting with **Fleet Manager Pro (LAN)** works too.)

Your data lives in the `data` folder next to the program. **READ ME - Your Data.txt** explains where that is, how to
move it to a new version or PC, and how to back it up.

Installation help: <http://fleetmanager.servehttp.com:8080/install.html> (also in **Installation Guide.pdf**).
What every feature does and how to use it, searchable: <http://fleetmanager.servehttp.com:8080/features.html>.
Questions and troubleshooting: <http://fleetmanager.servehttp.com:8080/faq.html>.

Remote drivers stream their own game with (token and fingerprint are on their profile page):

    "Fleet Manager Pro.exe" agent --server https://HOST-PC:8760 --token TOKEN --pin FINGERPRINT
''', readme, flags=re.S)
    open(os.path.join(OUT, "README.md"), "w", encoding="utf-8", newline="\n").write(readme)
    with open(os.path.join(HERE, "Guides", DATA_README), encoding="utf-8") as f:       # where the data lives, how to move it
        text = f.read().replace("{VERSION}", version)
    with open(os.path.join(OUT, DATA_README), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)
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
    print(f"Public build, update channel (build tag): {tag}")


if __name__ == "__main__":
    main()
