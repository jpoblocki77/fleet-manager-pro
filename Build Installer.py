r"""Make the one-file installer for buyer copies that are already built.

    python "Build Installer.py" FMP-AAAA-...          one or more buyers
    python "Build Installer.py" --all                 every "For Buyer - FMP-..." folder in Build Output
    python "Build Installer.py" --public              the public build (For Everyone)

Out:  Build Output\Installers\<purchase key>\Fleet Manager Pro Setup.exe    (send each buyer only their own)
      Build Output\Installers\Public\Fleet Manager Pro Setup.exe            (the public build: for anyone)
Needs Inno Setup 6. Run it after the copies are built: it packs the finished folder, so the copy stays sealed to its key.
"""
import argparse
import os
import re
import subprocess  # nosec B404  # runs the Inno Setup compiler
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(os.path.dirname(HERE), "Build Output")
ISS = os.path.join(HERE, "Fleet Manager Pro", "tools", "installer.iss")
KEY_RX = re.compile(r"FMP(-[A-Z0-9]{4}){4}")
ISCC = [os.path.join(os.environ.get(v, ""), *p, "Inno Setup 6", "ISCC.exe")
        for v, p in (("LOCALAPPDATA", ("Programs",)), ("ProgramFiles(x86)", ()), ("ProgramFiles", ()))]


PUBLIC = "Public"


def build(key, output=OUTPUT, out=None):
    src = os.path.join(output, "For Everyone" if key == PUBLIC else f"For Buyer - {key}")
    for need in ("Fleet Manager Pro.exe", "VERSION", "Licence Agreement.txt", "Installation Guide.pdf", "READ ME - Your Data.txt"):
        if not os.path.isfile(os.path.join(src, need)):
            sys.exit(f"{src}: no {need} - build this copy first")
    if os.path.exists(os.path.join(src, "data")):
        sys.exit(f"{src} has a data folder (it has been run). Build the copy again first; data must never be shipped.")
    with open(os.path.join(src, "VERSION"), encoding="utf-8") as fh:
        ver = fh.read().strip()
    num = ".".join((re.findall(r"\d+", ver) + ["0"] * 4)[:4])
    out = out or os.path.join(output, "Installers", key)
    os.makedirs(out, exist_ok=True)
    iscc = next((p for p in ISCC if os.path.isfile(p)), None) or sys.exit("Inno Setup 6 isn't installed")
    r = subprocess.run([iscc, "/Q", f"/DFmpSrc={src}", f"/DFmpVer={ver}", f"/DFmpNum={num}", f"/DFmpOut={out}", ISS],  # nosec B603  # fixed argument list, no shell
                       capture_output=True, text=True)
    exe = os.path.join(out, "Fleet Manager Pro Setup.exe")
    if r.returncode or not os.path.isfile(exe):
        sys.exit(f"Inno Setup failed for {key}:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    print(f"OK  {key}  {ver}  {os.path.getsize(exe) / 1e6:.1f} MB  {exe}")
    return exe


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--public", action="store_true", help="the public build (For Everyone)")
    ap.add_argument("--output", default=OUTPUT, help="the Build Output folder")
    ap.add_argument("--out", help="write the installer here (one key only)")
    a = ap.parse_args()
    keys = [k.strip().upper() for k in a.keys]
    if a.all:
        keys = sorted(d[len("For Buyer - "):] for d in os.listdir(a.output)
                      if d.startswith("For Buyer - ") and os.path.isdir(os.path.join(a.output, d)))
    if (not keys and not a.public) or any(not KEY_RX.fullmatch(k) for k in keys) or (a.out and len(keys) + a.public != 1):
        ap.error("name one or more purchase keys, or use --all / --public")
    for k in keys + ([PUBLIC] if a.public else []):
        build(k, a.output, a.out)
