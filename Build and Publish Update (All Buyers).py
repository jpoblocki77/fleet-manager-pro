r"""Seller tool: ship ONE new version to EVERY buyer in a single run.

Because each buyer's copy has its pages sealed to that buyer, an update is still a per-buyer build - but you
never do that by hand. This rebuilds every buyer's copy at the current VERSION and publishes each one to the
licence server's update folder, so each buyer's app then shows "Update available".

    python "Build and Publish Update (All Buyers).py"                 every active buyer on the licence server
    python "Build and Publish Update (All Buyers).py" --from-csv      ...or the old local Buyer Builds (PRIVATE).csv
    python "Build and Publish Update (All Buyers).py" --stock         ...and a fresh stock copy too
    python "Build and Publish Update (All Buyers).py" --public        ...and the public build (For Everyone)
    python "Build and Publish Update (All Buyers).py" --public --only-public   just the public build
    python "Build and Publish Update (All Buyers).py" --keys FMP-AAAA-... FMP-BBBB-...   just these buyers
    python "Build and Publish Update (All Buyers).py" --notes "Fixes the map." --dry-run

Steps, per buyer: build the .exe copy (Build Pro Buyer Copy.py --buyer KEY) and publish it (publish_update.py
--zip ... --key KEY --flavor exe), so the buyer's copy updates itself. Only the .exe build is made now - the no-exe
build was retired. Bump Fleet Manager Pro\VERSION FIRST, or the server won't see the rebuild as newer.
"""
import argparse
import csv
import os
import subprocess  # nosec B404  # runs our own build/publish scripts
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
BUILD_OUTPUT = os.path.join(os.path.dirname(DEV), "Build Output")
BUILD_SCRIPT = os.path.join(DEV, "Build Pro Buyer Copy.py")
# (flavour, build script, buyer zip name, stock zip name) - the .exe build is the only one shipped now.
FLAVORS = (
    ("exe", BUILD_SCRIPT, "For Buyer - {key}.zip", "For Buyer.zip"),
)
PUBLIC_ZIP = "For Everyone.zip"
HOST = os.path.join(DEV, "Fleet Manager License Host")
PUBLISH = os.path.join(HOST, "publish_update.py")
CSV_PATH = os.path.join(DEV, "Buyer Builds (PRIVATE).csv")
KEY_RX = __import__("re").compile(r"^FMP-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")


def version():
    with open(os.path.join(DEV, "Fleet Manager Pro", "VERSION"), encoding="utf-8") as fh:
        return fh.read().strip()


def buyers_from_csv():
    """Unique purchase keys the seller has built before (the private ledger)."""
    keys = []
    if not os.path.isfile(CSV_PATH):
        return keys
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            key = (row[1] or "").strip().upper()
            if KEY_RX.match(key) and key not in keys:
                keys.append(key)
    return keys


def buyers_from_server():
    """Active (not revoked) Fleet Manager Pro purchase keys, straight from the licence server's database - the
    single source of truth. Uses License Host\\server_config.json (admin_url + admin_token), so it works from this
    PC over the home network; nothing needs to move to the server PC."""
    sys.path.insert(0, HOST)
    import license_tool  # noqa: E402  # lives in the License Host folder
    keys = []
    for p in license_tool.Admin(HOST).purchases():
        key = (p.get("key") or "").strip().upper()
        if p.get("revoked") or (p.get("product") or "Fleet Manager Pro") != "Fleet Manager Pro":
            continue
        if KEY_RX.match(key) and key not in keys:
            keys.append(key)
    return keys


def run(cmd, cwd):
    print("    $", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    return subprocess.run(cmd, cwd=cwd).returncode  # nosec B603  # fixed argument list, no shell


def _build_one(flavor, script, zip_path, notes, key=None, stock=False, public=False):
    if public:
        rc = run([sys.executable, script, "--public"], DEV)
        pub = [sys.executable, PUBLISH, "--zip", zip_path, "--public", "--flavor", flavor]
    elif stock:
        rc = run([sys.executable, script], DEV)
        pub = [sys.executable, PUBLISH, "--zip", zip_path, "--stock", "--flavor", flavor]
    else:
        rc = run([sys.executable, script, "--buyer", key], DEV)
        pub = [sys.executable, PUBLISH, "--zip", zip_path, "--key", key, "--flavor", flavor]
    if rc != 0:
        return f"{flavor} build failed (exit {rc})"
    if not os.path.isfile(zip_path):
        return f"{flavor} build produced no zip ({zip_path})"
    if notes:
        pub += ["--notes", notes]
    rc = run(pub, HOST)
    return None if rc == 0 else f"{flavor} publish failed (exit {rc})"


def build_and_publish(key, notes, stock=False, public=False):
    """Build and publish this buyer's .exe copy. Returns None when it published, else a summary of what failed."""
    errs = []
    for flavor, script, buyer_tmpl, stock_name in FLAVORS:
        name = PUBLIC_ZIP if public else stock_name if stock else buyer_tmpl.format(key=key)
        zip_path = os.path.join(BUILD_OUTPUT, name)
        err = _build_one(flavor, script, zip_path, notes, key=None if stock or public else key, stock=stock,
                         public=public)
        if err:
            errs.append(err)
    return "; ".join(errs) if errs else None


def main():
    ap = argparse.ArgumentParser(description="Build + publish this version to every buyer")
    ap.add_argument("--keys", nargs="*", help="only these purchase keys (default: every active key on the licence server)")
    ap.add_argument("--from-csv", action="store_true", help="use the old local Buyer Builds (PRIVATE).csv instead of the server")
    ap.add_argument("--stock", action="store_true", help="also build + publish a stock copy")
    ap.add_argument("--public", action="store_true", help="also build + publish the public build (For Everyone)")
    ap.add_argument("--only-public", action="store_true", help="with --public: skip the buyers' own copies")
    ap.add_argument("--notes", default="", help="release notes shown to every buyer")
    ap.add_argument("--dry-run", action="store_true", help="list what would be built, do nothing")
    ap.add_argument("--yes", action="store_true", help="don't ask before starting")
    a = ap.parse_args()

    ver = version()
    if a.only_public and not a.public:
        sys.exit("--only-public goes with --public")
    if a.only_public:
        keys, source = [], "--only-public"
    elif a.keys:
        keys, source = [k.strip().upper() for k in a.keys], "--keys"
    elif a.from_csv:
        keys, source = buyers_from_csv(), os.path.basename(CSV_PATH)
    else:
        try:
            keys, source = buyers_from_server(), "licence server"
        except Exception as e:
            sys.exit(f"Couldn't get the buyer list from the licence server: {e}\n"
                     "Is License Server running on this PC? (Or pass --keys / --from-csv.)")
    bad = [k for k in keys if not KEY_RX.match(k)]
    if bad:
        sys.exit(f"Not purchase keys: {bad}")
    if not keys and not a.stock and not a.public:
        sys.exit(f"No buyers found ({source}). Sell a copy first, or pass --keys / --stock.")

    print(f"Version to ship: Fleet Manager Pro {ver}")
    print(f"Buyers to update: {len(keys)}  (from {source})" + ("  (+ stock copy)" if a.stock else "")
          + ("  (+ public build)" if a.public else ""))
    for k in keys:
        print("  -", k)
    if a.dry_run:
        print("\n(dry run - nothing built)")
        return
    if not a.yes:
        ans = input(f"\nBuild and publish {len(keys) + a.stock + a.public} copies? Each takes a few minutes. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    results = []
    todo = ([(k, "") for k in keys] + ([("STOCK", "stock")] if a.stock else [])
            + ([("PUBLIC", "public")] if a.public else []))
    for i, (key, kind) in enumerate(todo, 1):
        who = {"stock": "stock copy", "public": "public build"}.get(kind, key)
        print(f"\n[{i}/{len(todo)}] {who}")
        try:
            err = build_and_publish(key, a.notes, stock=kind == "stock", public=kind == "public")
        except Exception as e:  # keep going for the other buyers
            err = f"error: {e}"
        results.append((who, err))

    print("\n" + "=" * 60 + "\n  Summary")
    ok = [w for w, e in results if e is None]
    fail = [(w, e) for w, e in results if e is not None]
    for w in ok:
        print(f"  OK    {w}")
    for w, e in fail:
        print(f"  FAIL  {w}  - {e}")
    print(f"\n  {len(ok)} published, {len(fail)} failed. Version {ver}.")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
