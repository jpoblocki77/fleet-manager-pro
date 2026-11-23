"""Seller tool (used by the build scripts): seal a buyer copy's pages to one purchase.

* Every buyer copy gets a hidden build tag (FMPB-XXXX-XXXX-XXXX-XXXX) worked out from the purchase key with a secret
  only the licence server knows. The tag can't be turned back into the key, but the licence server can tell whose
  copy it is (License Manager > 10) Identify a copy).
* The program's pages are encrypted with a key that belongs to that build tag. The licence server hands the key back
  inside the signed licence, so a cracked copy that skips the licence check has no pages to show, and a key copied
  out of one buyer's licence only fits that buyer's copy.
* The screens needed before activation (activate, licence agreement, error page) stay readable.

The secret comes from the licence server's signing key (host_key.json in the private License Host folder), so the
server and the build PC agree without any extra file.
"""
import base64
import hashlib
import hmac
import importlib.util
import json
import os

# pages that must work before the program is activated (they hold no program logic)
PUBLIC_PAGES = {"auth_base.html", "_macros.html", "activate.html", "eula.html", "error.html"}
KEY_PREFIXES = ("FMP-",)


def master_from_hostkey(path):
    with open(path, encoding="utf-8") as fh:
        k = json.load(fh)
    return k["public"], hmac.new(bytes.fromhex(k["private"]), b"fmp-unlock-master-v1", hashlib.sha256).digest()


def build_tag(master, purchase_key):
    """FMPB-XXXX-XXXX-XXXX-XXXX for a purchase key (or 'STOCK' for an unassigned copy)."""
    raw = hmac.new(master, b"build|" + purchase_key.strip().upper().encode(), hashlib.sha256).digest()
    code = base64.b32encode(raw).decode()[:16]
    return "FMPB-" + "-".join(code[i:i + 4] for i in range(0, 16, 4))


def page_key(master, tag):
    return hmac.new(master, b"pages|" + tag.encode(), hashlib.sha256).digest()


def _licensing(fmpro_dir):
    spec = importlib.util.spec_from_file_location("_fmp_licensing", os.path.join(fmpro_dir, "licensing.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seal_templates(fmpro_dir, master, tag):
    """Write fmpro/_embedded_templates.py: public pages in plain text, every other page encrypted for this build."""
    lic = _licensing(fmpro_dir)
    key = page_key(master, tag)
    tdir = os.path.join(fmpro_dir, "templates")
    plain, sealed = {}, {}
    for root, _d, files in os.walk(tdir):
        for f in files:
            full = os.path.join(root, f)
            name = os.path.relpath(full, tdir).replace(os.sep, "/")
            text = open(full, encoding="utf-8").read()
            if name in PUBLIC_PAGES:
                plain[name] = text
            else:
                sealed[name] = lic.seal(key, text.encode("utf-8")).hex()
    if len(sealed) < 10 or set(plain) != PUBLIC_PAGES:
        raise SystemExit("Couldn't find the page templates to seal")
    with open(os.path.join(fmpro_dir, "_embedded_templates.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"BUILD = {tag!r}\nCHECK = {lic.pages_check(key)!r}\nTEMPLATES = {plain!r}\nSEALED = {sealed!r}\n")
    return len(plain), len(sealed)


PUBLIC_BUILD = "PUBLIC"   # the one build everyone downloads: its tag is build_tag(master, PUBLIC_BUILD)


def open_templates(fmpro_dir, tag):
    """Write fmpro/_embedded_templates.py for the public build: every page in plain text, nothing sealed.
    The copy still carries a build tag so the licence server knows which update channel it is on."""
    tdir = os.path.join(fmpro_dir, "templates")
    plain = {}
    for root, _d, files in os.walk(tdir):
        for f in files:
            full = os.path.join(root, f)
            plain[os.path.relpath(full, tdir).replace(os.sep, "/")] = open(full, encoding="utf-8").read()
    if len(plain) < 10 or not PUBLIC_PAGES <= set(plain):
        raise SystemExit("Couldn't find the page templates")
    with open(os.path.join(fmpro_dir, "_embedded_templates.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"BUILD = {tag!r}\nCHECK = None\nTEMPLATES = {plain!r}\nSEALED = {{}}\n")
    return len(plain)
