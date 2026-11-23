r"""Make the one-file installer for the public copy once it's built.

    python "Build Installer.py"            (--public is still accepted; it's the only kind now)

Out:  Build Output\Installers\Public\Fleet Manager Pro Setup.exe    (anyone can have it: Fleet Manager Pro is free)
Needs Inno Setup 6. Run it after  Build Pro Buyer Copy.py : it packs the finished  Build Output\For Everyone  folder.
"""
import argparse
import os
import re
import subprocess  # nosec B404  # runs the Inno Setup compiler
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(os.path.dirname(HERE), "Build Output")
ISS = os.path.join(HERE, "Fleet Manager Pro", "tools", "installer.iss")
ISCC = [os.path.join(os.environ.get(v, ""), *p, "Inno Setup 6", "ISCC.exe")
        for v, p in (("LOCALAPPDATA", ("Programs",)), ("ProgramFiles(x86)", ()), ("ProgramFiles", ()))]


def build(output=OUTPUT, out=None):
    src = os.path.join(output, "For Everyone")
    for need in ("Fleet Manager Pro.exe", "VERSION", "Licence Agreement.txt", "Installation Guide.pdf", "READ ME - Your Data.txt"):
        if not os.path.isfile(os.path.join(src, need)):
            sys.exit(f"{src}: no {need} - build the copy first (Build Pro Buyer Copy.py)")
    if os.path.exists(os.path.join(src, "data")):
        sys.exit(f"{src} has a data folder (it has been run). Build the copy again first; data must never be shipped.")
    with open(os.path.join(src, "VERSION"), encoding="utf-8") as fh:
        ver = fh.read().strip()
    num = ".".join((re.findall(r"\d+", ver) + ["0"] * 4)[:4])
    out = out or os.path.join(output, "Installers", "Public")
    os.makedirs(out, exist_ok=True)
    iscc = next((p for p in ISCC if os.path.isfile(p)), None) or sys.exit("Inno Setup 6 isn't installed")
    r = subprocess.run([iscc, "/Q", f"/DFmpSrc={src}", f"/DFmpVer={ver}", f"/DFmpNum={num}", f"/DFmpOut={out}", ISS],  # nosec B603  # fixed argument list, no shell
                       capture_output=True, text=True)
    exe = os.path.join(out, "Fleet Manager Pro Setup.exe")
    if r.returncode or not os.path.isfile(exe):
        sys.exit(f"Inno Setup failed:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    print(f"OK  Public  {ver}  {os.path.getsize(exe) / 1e6:.1f} MB  {exe}")
    return exe


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Make Fleet Manager Pro Setup.exe from the built public copy")
    ap.add_argument("--public", action="store_true", help="(the default - kept so older commands still work)")
    ap.add_argument("--output", default=OUTPUT, help="the Build Output folder")
    ap.add_argument("--out", help="write the installer here")
    a = ap.parse_args()
    build(a.output, a.out)
