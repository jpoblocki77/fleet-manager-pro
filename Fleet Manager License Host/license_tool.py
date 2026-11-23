"""Fleet Manager - License Manager (seller only; never ship this folder).

Admin tool for the online licence server (license_server.py). The server must be running (License Server.bat or
license-server.sh, or wherever you deployed it). On Linux run ./license-manager.sh instead of python.

    python license_tool.py                                   interactive menu
    python license_tool.py setup --app "PATH" --url URL      signing key + admin token; point the app at URL
    python license_tool.py new "Name" [email]                new Fleet Manager Pro purchase -> key FMP-XXXX-... (2 PCs)
    python license_tool.py new-pro "Name" [email]            new Fleet Manager Pro purchase -> key FMP-XXXX-...
    python license_tool.py list                              purchases and their PCs
    python license_tool.py release KEY FMID-...                free a PC slot (buyer replaced a PC)
    python license_tool.py revoke KEY  |  restore KEY        block / unblock a purchase on every PC
    python license_tool.py reset KEY                         free ALL PCs on a purchase (buyer couldn't deactivate)
    python license_tool.py delete KEY                        delete a purchase for good (active or revoked)
    python license_tool.py delete-revoked                    delete every revoked purchase
    python license_tool.py vtc                               VTC Finder listings and reports
    python license_tool.py vtc-hide KEY  |  vtc-unhide KEY   hide / show a company in the VTC Finder
    python license_tool.py vtc-clear KEY                     forget the reports about a company
    python license_tool.py whose FMPB-XXXX-XXXX-XXXX-XXXX     which buyer a copy was built for (its hidden build tag)
"""
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ed25519  # noqa: E402


class LicenceError(Exception):
    pass


def setup(folder, app_folder=None, url=None):
    """Create the signing key and admin token (once), and point a Fleet Manager copy at this licence server."""
    key_path = os.path.join(folder, "host_key.json")
    made = False
    if not os.path.exists(key_path):
        secret = secrets.token_bytes(32)
        with open(key_path, "w", encoding="utf-8") as fh:
            json.dump({"private": secret.hex(), "public": ed25519.public_key(secret).hex(),
                       "warning": "PRIVATE SIGNING KEY. Back up, never share. Anyone with it can issue licences."}, fh, indent=2)
        made = True
    pub = json.load(open(key_path, encoding="utf-8"))["public"]
    cfg_path = os.path.join(folder, "server_config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    cfg.setdefault("admin_token", secrets.token_urlsafe(24))
    cfg.setdefault("port", 8750)
    cfg.setdefault("lease_days", 7)
    if url:
        cfg["public_url"] = url.rstrip("/")
        if url.startswith("https://") and os.path.exists(os.path.join(folder, "tls_cert.pem")):
            # Home server: License Manager on this PC keeps talking to the server directly (the router usually
            # can't loop back to the internet address).
            cfg["admin_url"] = f"http://127.0.0.1:{cfg.get('port', 8750)}"
    cfg.setdefault("public_url", f"http://127.0.0.1:{cfg['port']}")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    if app_folder:
        path = os.path.join(app_folder, "licensing.py")
        if not os.path.exists(path):                       # Fleet Manager Pro keeps it in its fmpro package
            path = os.path.join(app_folder, "fmpro", "licensing.py")
        if not os.path.exists(path):
            raise LicenceError(f"Can't find licensing.py in {app_folder}")
        src = open(path, encoding="utf-8").read()
        pin = ""
        cert = os.path.join(folder, "tls_cert.pem")
        if cfg["public_url"].startswith("https://") and os.path.exists(cert):
            with open(cert, encoding="ascii") as fh:      # pin this server's own certificate (home server)
                pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(fh.read())).hexdigest()
        src, n1 = re.subn(r'PUBLIC_KEY_HEX = "[0-9a-f]{64}"', f'PUBLIC_KEY_HEX = "{pub}"', src)
        src, n2 = re.subn(r'SERVER_URL = "[^"]*"', f'SERVER_URL = "{cfg["public_url"]}"', src)
        src, n3 = re.subn(r'TLS_PIN = "[0-9a-f]*"', f'TLS_PIN = "{pin}"', src)
        if n1 != 1 or n2 != 1 or n3 != 1:
            raise LicenceError("Couldn't find PUBLIC_KEY_HEX / SERVER_URL / TLS_PIN in licensing.py "
                               "(is that Fleet Manager copy up to date?)")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
    return {"public": pub, "created": made, "url": cfg["public_url"], "token": cfg["admin_token"]}


class Admin:
    """Talks to the licence server's admin API."""

    def __init__(self, folder=HERE, url=None, token=None):
        cfg_path = os.path.join(folder, "server_config.json")
        cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
        self.url = (url or cfg.get("admin_url") or cfg.get("public_url") or "http://127.0.0.1:8750").rstrip("/")
        if not self.url.lower().startswith(("http://", "https://")):
            raise LicenceError(f"The licence server address must start with http:// or https:// (got {self.url})")
        self.token = token or cfg.get("admin_token")
        if not self.token:
            raise LicenceError("Not set up yet: choose 'Set up' first.")

    def _call(self, path, body=None):
        req = urllib.request.Request(self.url + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310  # http(s) only, checked in __init__
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                raise LicenceError(json.loads(e.read()).get("error", f"HTTP {e.code}"))
            except ValueError:
                raise LicenceError(f"HTTP {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise LicenceError(f"Can't reach the licence server at {self.url}. Is License Server running? ({e})")

    def new_purchase(self, name, email="", product="Fleet Manager Pro"):
        return self._call("/v1/admin/purchase", {"name": name, "email": email, "product": product})["key"]

    def purchases(self):
        return self._call("/v1/admin/list")["purchases"]

    def release(self, key, machine):
        self._call("/v1/admin/release", {"key": key.strip().upper(), "machine": machine.strip().upper()})

    def revoke(self, key, revoked=True):
        self._call("/v1/admin/revoke", {"key": key.strip().upper(), "revoked": revoked})

    def delete(self, key):
        """Delete one purchase (active or revoked) for good. Returns what was deleted."""
        return self._call("/v1/admin/delete", {"key": key.strip().upper()})["deleted"]

    def whose(self, tag):
        """Which purchase a copy's hidden build tag belongs to: a dict, "STOCK", or None."""
        return self._call("/v1/admin/whose", {"tag": tag.strip().upper()})["owner"]

    def delete_revoked(self):
        """Delete every revoked purchase. Returns what was deleted."""
        return self._call("/v1/admin/delete", {"all_revoked": True})["deleted"]

    def vtc(self):
        """VTC Finder listings with their reports, most reported first."""
        return self._call("/v1/admin/vtc")["listings"]

    def vtc_hide(self, key, hidden=True):
        self._call("/v1/admin/vtc_hide", {"key": key.strip().upper(), "hidden": hidden})

    def vtc_clear(self, key):
        return self._call("/v1/admin/vtc_clear", {"key": key.strip().upper()})["cleared"]

    def reset(self, key):
        """Free every PC on a purchase (buyer couldn't deactivate). Returns the Machine IDs that were freed."""
        return self._call("/v1/admin/reset", {"key": key.strip().upper()})["freed"]


def _print(admin):
    rows = admin.purchases()
    if not rows:
        print("  No purchases yet.")
    for p in rows:
        flag = "  ** REVOKED **" if p.get("revoked") else ""
        resets = f"  reset {p['resets']}x (last {p['last_reset'][:10]})" if p.get("resets") else ""
        print(f"  [{p.get('product') or 'Fleet Manager'}]  {p['key']}  {p['name']}{' <' + p['email'] + '>' if p['email'] else ''}  ({len(p['pcs'])}/{p['slots']} PCs){resets}{flag}")
        for a in p["pcs"]:
            print(f"      PC{a['slot']}: {a['machine']}  last check {(a.get('last_seen') or a['issued'])[:16].replace('T', ' ')} UTC")
            if a.get("build_owner"):
                print(f"        ! LEAK? this PC runs a copy built for {a['build_owner']}, not for this purchase")


def _print_vtc(admin):
    rows = admin.vtc()
    if not rows:
        print("  No companies in the VTC Finder yet. A company lists itself in the app: Company > VTC Profile.")
    for v in rows:
        flag = "  ** HIDDEN **" if v["hidden"] else ""
        flag += "" if v["listed"] else "  (not listed by its owner)"
        print(f"  {v['key']}  {v['company']}  /vtc/{v['code']}  updated {(v['updated'] or '')[:10]}  {len(v['reports'])} report(s){flag}")
        for r in v["reports"][:10]:
            print(f"      {r['created'][:10]}  {r['reason']}{': ' + r['note'] if r['note'] else ''}")


def menu():
    default_app = os.path.join(os.path.dirname(HERE), "Fleet Manager Pro")
    while True:
        print("\n=== Fleet Manager Pro - License Manager (online) ===")
        try:
            admin = Admin()
            print(f"Licence server: {admin.url}")
        except LicenceError:
            admin = None
            print("Not set up yet - choose 1.")
        print(" 1) Set up / change server address (and point Fleet Manager Pro at it)\n 2) New purchase (FMP- key, older copies only)\n 3) List purchases + PCs\n"
              " 4) Release a PC (buyer replaced a PC)\n 5) Block a company from the online features\n 6) Unblock a company\n"
              " 7) Reset a purchase's uses (frees all PCs - buyer couldn't deactivate)\n"
              " 8) Delete a purchase (active or revoked)\n 9) Delete ALL revoked purchases\n"
              "10) Identify a copy (whose is it? - by its build tag)\n11) VTC Finder listings and reports (hide or show a company)\n 0) Quit")
        c = input("> ").strip()
        try:
            if c == "1":
                url = input("  Internet address buyers' copies will use [http://127.0.0.1:8750]: ").strip() or None
                app = input(f"  Fleet Manager Pro folder to configure [{default_app}]: ").strip() or default_app
                r = setup(HERE, app, url)
                print(f"  Signing key {'created' if r['created'] else 'kept'}. Fleet Manager Pro now checks {r['url']}.")
            elif admin and c == "2":
                product = "Fleet Manager Pro"                     # the only product this server licenses
                key = admin.new_purchase(input("  Buyer name: "), input("  Buyer email (optional): "), product)
                print(f"\n  {product} purchase key: {key}  (works on 2 PCs). Give it to the buyer; they type it in {product}.")
            elif admin and c == "3":
                _print(admin)
            elif admin and c == "4":
                admin.release(input("  Purchase key: "), input("  Machine ID to release: "))
                print("  Released.")
            elif admin and c == "7":
                key = input("  Purchase key to reset: ").strip().upper()
                p = next((x for x in admin.purchases() if x["key"] == key), None)
                if not p:
                    print("  ! No purchase with that key")
                    continue
                pcs = ", ".join(f"PC{a['slot']} {a['machine']}" for a in p["pcs"]) or "none"
                print(f"  {p['name']}: {len(p['pcs'])}/{p['slots']} PCs in use ({pcs}). Reset {p.get('resets') or 0}x before.")
                if input("  Free ALL PCs on this purchase? Those PCs lock at their next check. Type YES: ").strip() != "YES":
                    print("  Cancelled - nothing changed.")
                    continue
                freed = admin.reset(key)
                print(f"  Reset: {len(freed)} PC(s) freed. The buyer can now activate {p['slots']} PCs again.")
            elif admin and c == "8":
                key = input("  Purchase key to delete: ").strip().upper()
                p = next((x for x in admin.purchases() if x["key"] == key), None)
                if not p:
                    print("  ! No purchase with that key")
                    continue
                state = "REVOKED" if p.get("revoked") else f"ACTIVE on {len(p['pcs'])} PC(s)"
                print(f"  {p['key']}  {p['name']}{' <' + p['email'] + '>' if p.get('email') else ''}  - {state}")
                if not p.get("revoked"):
                    print("  This key is still in use: those PCs lock at their next check and the key stops working for good.")
                if input("  Delete this purchase for good? Type DELETE: ").strip() != "DELETE":
                    print("  Cancelled - nothing changed.")
                    continue
                admin.delete(key)
                print("  Deleted. (A copy was saved to deleted_purchases.jsonl on the licence server.)")
            elif admin and c == "10":
                print("  Get the tag from the copy: open a Command Prompt in its folder and run")
                print('    "Fleet Manager Pro.exe" --build-tag              (single-program version)')
                print(r'    runtime\python.exe app\run.pyc --build-tag   (standard version)')
                tag = input("  Build tag (FMPB-...): ").strip().upper()
                w = admin.whose(tag) if tag else None
                if w == "STOCK":
                    print("  An unassigned (stock) copy - not built for a particular buyer.")
                elif w:
                    print(f"  Built for: {w['key']}  {w['name']}{' <' + w['email'] + '>' if w.get('email') else ''}"
                          f"{'  (REVOKED)' if w.get('revoked') else ''}")
                else:
                    print("  ! No purchase matches that tag (typo, a deleted purchase, or not one of your copies).")
            elif admin and c == "11":
                _print_vtc(admin)
                if not admin.vtc():
                    continue
                key = input("  Company ID (FMC-...) or old purchase key to change (Enter = none): ").strip().upper()
                if not key:
                    continue
                what = input("  H) hide it   S) show it again   C) forget its reports: ").strip().lower()
                if what in ("h", "s"):
                    admin.vtc_hide(key, what == "h")
                    print("  Hidden from the VTC Finder. It stays hidden when the company edits its profile." if what == "h" else "  Shown again.")
                elif what == "c":
                    print(f"  {admin.vtc_clear(key)} report(s) forgotten.")
                else:
                    print("  Nothing changed.")
            elif admin and c == "9":
                revoked = [x for x in admin.purchases() if x.get("revoked")]
                if not revoked:
                    print("  No revoked purchases.")
                    continue
                for x in revoked:
                    print(f"  {x['key']}  {x['name']}  (revoked {x['revoked'][:10]})")
                if input(f"  Delete these {len(revoked)} revoked purchase(s) for good? Type DELETE: ").strip() != "DELETE":
                    print("  Cancelled - nothing changed.")
                    continue
                print(f"  Deleted {len(admin.delete_revoked())} revoked purchase(s). (Copies saved to deleted_purchases.jsonl.)")
            elif admin and c in ("5", "6"):
                admin.revoke(input("  Company ID (FMC-...) or old purchase key: ").strip().upper(), c == "5")
                print("  Blocked from the Apply page, Global Leaderboard and VTC Finder (the app itself keeps working)."
                      if c == "5" else "  Unblocked.")
            elif c in ("0", "q", ""):
                return
        except LicenceError as e:
            print(f"  ! {e}")


def main(argv):
    if not argv:
        return menu()
    cmd, a = argv[0], argv[1:]
    try:
        if cmd == "setup":
            app = a[a.index("--app") + 1] if "--app" in a else None
            url = a[a.index("--url") + 1] if "--url" in a else None
            r = setup(HERE, app, url)
            print(f"Signing key {'created' if r['created'] else 'kept'}; public key {r['public']}\nLicence server address: {r['url']}")
        elif cmd == "new":
            print(Admin().new_purchase(a[0], a[1] if len(a) > 1 else ""))
        elif cmd == "new-pro":
            print(Admin().new_purchase(a[0], a[1] if len(a) > 1 else "", "Fleet Manager Pro"))
        elif cmd == "list":
            _print(Admin())
        elif cmd == "release":
            Admin().release(a[0], a[1])
            print("released")
        elif cmd == "reset":
            freed = Admin().reset(a[0])
            print(f"reset: {len(freed)} PC(s) freed" + (f" ({', '.join(freed)})" if freed else ""))
        elif cmd == "delete":
            d = Admin().delete(a[0])[0]
            print(f"deleted {d['key']} ({d['name']})")
        elif cmd == "whose":
            w = Admin().whose(a[0])
            print("stock copy (no buyer)" if w == "STOCK" else (f"{w['key']} {w['name']}" if w else "no match"))
        elif cmd == "vtc":
            _print_vtc(Admin())
        elif cmd in ("vtc-hide", "vtc-unhide"):
            Admin().vtc_hide(a[0], cmd == "vtc-hide")
            print("hidden" if cmd == "vtc-hide" else "shown")
        elif cmd == "vtc-clear":
            print(f"{Admin().vtc_clear(a[0])} report(s) forgotten")
        elif cmd == "delete-revoked":
            d = Admin().delete_revoked()
            print(f"deleted {len(d)} revoked purchase(s)" + (": " + ", ".join(x["key"] for x in d) if d else ""))
        elif cmd in ("revoke", "restore"):
            Admin().revoke(a[0], cmd == "revoke")
            print(cmd + "d")
        else:
            print(__doc__)
    except LicenceError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
