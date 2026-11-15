r"""Seller tool: put a freshly built copy where the licence server offers it to that buyer as an update.

The licence server hands each running copy a *signed* offer of the newest build in its own update folder. This
tool just drops a built package into the right folder; the server signs the manifest (with host_key.json) when a
copy asks, so there is no signing step here to get wrong.

    python publish_update.py --zip "...\Build Output\For Buyer - FMP-XXXX....zip" --key FMP-XXXX-XXXX-XXXX-XXXX
    python publish_update.py --zip "...\Build Output\For Buyer.zip" --stock
    python publish_update.py --zip "...package 1.4.zip" --tag FMPB-XXXX-XXXX-XXXX-XXXX --notes "Fixes the map."

* --key    the buyer's purchase key (the folder is found from it, exactly as the build does)
* --stock  a stock copy (no buyer)        * --tag  a build tag straight from the build output / the CSV
* --generic  the unsealed developer/seller copy

The package's filename MUST contain its version (e.g. ...1.4.zip or Fleet Manager Pro 1.4.2.zip); that version is
how the server knows it's newer than what a buyer is running.
"""
import argparse
import json
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from license_server import build_tag, unlock_master  # noqa: E402

VERSION_RX = re.compile(r"(\d+(?:\.\d+)+)")


def tag_for_key(key):
    with open(os.path.join(HERE, "host_key.json"), encoding="utf-8") as fh:
        private = json.load(fh)["private"]
    return build_tag(unlock_master(private), key.strip().upper())


def version_from_zip(path):
    """Read the VERSION file packed inside a built copy, so we don't rely on the zip's name."""
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if os.path.basename(name) == "VERSION":
                    return z.read(name).decode("utf-8", "replace").strip() or None
    except (OSError, zipfile.BadZipFile):
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description="Publish a Fleet Manager Pro update to the licence server")
    ap.add_argument("--zip", required=True, help="the built package (.zip) to offer")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="the buyer's purchase key (FMP-XXXX-XXXX-XXXX-XXXX)")
    g.add_argument("--tag", help="the copy's build tag (FMPB-XXXX-XXXX-XXXX-XXXX)")
    g.add_argument("--stock", action="store_true", help="a stock copy (no buyer)")
    g.add_argument("--generic", action="store_true", help="the unsealed developer/seller copy")
    ap.add_argument("--notes", default="", help="short release notes shown to the buyer")
    ap.add_argument("--version", default="", help="override the version (otherwise read from the zip name or VERSION)")
    a = ap.parse_args()

    src = a.zip
    if not os.path.isfile(src):
        sys.exit(f"No such file: {src}")
    name = os.path.basename(src)
    if not name.lower().endswith(".zip"):
        sys.exit("The package must be a .zip file.")
    version = a.version.strip()
    if not version:
        mv = VERSION_RX.search(name)
        version = mv.group(1) if mv else version_from_zip(src)
    if not version:
        sys.exit(f"Couldn't tell the version of '{name}'. Pass --version 1.4 (or name the zip so it contains the version).")

    if a.stock:
        folder = "STOCK"
    elif a.generic:
        folder = "GENERIC"
    elif a.tag:
        folder = a.tag.strip().upper()
        if not re.fullmatch(r"FMPB-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}", folder):
            sys.exit("A build tag looks like FMPB-XXXX-XXXX-XXXX-XXXX.")
    else:
        key = a.key.strip().upper()
        if not re.fullmatch(r"FMP-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}", key):
            sys.exit("A purchase key looks like FMP-XXXX-XXXX-XXXX-XXXX.")
        folder = tag_for_key(key)

    dest_dir = os.path.join(HERE, "updates", folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest_name = f"Fleet Manager Pro {version}.zip"      # a clean name the server can always read the version from
    # Drop older packages so only the newest remains (the server picks the highest version anyway).
    for old in os.listdir(dest_dir):
        if old.lower().endswith(".zip") and old != dest_name:
            os.remove(os.path.join(dest_dir, old))
    dest = os.path.join(dest_dir, dest_name)
    shutil.copy2(src, dest)
    if a.notes.strip():
        with open(os.path.join(dest_dir, f"notes-{version}.txt"), "w", encoding="utf-8") as fh:
            fh.write(a.notes.strip()[:2000])

    print(f"Published Fleet Manager Pro {version} for '{folder}'.")
    print(f"  -> {dest}")
    print("The licence server offers it the next time that copy checks (start-up or within a day). "
          "Make sure license_server.py is running on this PC.")


if __name__ == "__main__":
    main()
