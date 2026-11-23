"""Seller tool (used by the build script): compile the pages into a built copy and give it its build tag.

* Every built copy carries a build tag (FMPB-XXXX-XXXX-XXXX-XXXX): its update channel on the Fleet Manager server.
  The public build everyone downloads has one fixed tag; buyers' older copies each had a tag made from their purchase
  key, and the server still serves those channels (that's how the older copies move over to the public build).
* Tags are worked out with a secret only the server knows, taken from its signing key (host_key.json in the private
  License Host folder), so the server and the build PC agree without any extra file.
* Pages used to be encrypted to each buyer's licence; Fleet Manager Pro is free now, so every page is plain.
"""
import base64
import hashlib
import hmac
import json
import os

PUBLIC_BUILD = "PUBLIC"   # the one build everyone downloads: its tag is build_tag(master, PUBLIC_BUILD)
NEEDED_PAGES = {"auth_base.html", "_macros.html", "agree.html", "eula.html", "error.html"}


def master_from_hostkey(path):
    with open(path, encoding="utf-8") as fh:
        k = json.load(fh)
    return k["public"], hmac.new(bytes.fromhex(k["private"]), b"fmp-unlock-master-v1", hashlib.sha256).digest()


def build_tag(master, name):
    """FMPB-XXXX-XXXX-XXXX-XXXX for PUBLIC_BUILD (or, for older copies, a purchase key or 'STOCK')."""
    raw = hmac.new(master, b"build|" + name.strip().upper().encode(), hashlib.sha256).digest()
    code = base64.b32encode(raw).decode()[:16]
    return "FMPB-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))


def open_templates(fmpro_dir, tag):
    """Write fmpro/_embedded_templates.py: every page in plain text, and the copy's build tag."""
    tdir = os.path.join(fmpro_dir, "templates")
    plain = {}
    for root, _d, files in os.walk(tdir):
        for f in files:
            full = os.path.join(root, f)
            plain[os.path.relpath(full, tdir).replace(os.sep, "/")] = open(full, encoding="utf-8").read()
    if len(plain) < 10 or not NEEDED_PAGES <= set(plain):
        raise SystemExit("Couldn't find the page templates")
    with open(os.path.join(fmpro_dir, "_embedded_templates.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"BUILD = {tag!r}\nTEMPLATES = {plain!r}\n")
    return len(plain)
