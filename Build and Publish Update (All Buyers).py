r"""Seller tool: ship ONE new version to EVERY buyer in a single run.

Because each buyer's copy has its pages sealed to that buyer, an update is still a per-buyer build - but you
never do that by hand. This rebuilds every buyer's copy at the current VERSION and publishes each one to the
licence server's update folder, so each buyer's app then shows "Update available".

    python "Build and Publish Update (All Buyers).py"                 every buyer in Buyer Builds (PRIVATE).csv
    python "Build and Publish Update (All Buyers).py" --stock         ...and a fresh stock copy too
    python "Build and Publish Update (All Buyers).py" --keys FMP-AAAA-... FMP-BBBB-...   just these buyers
    python "Build and Publish Update (All Buyers).py" --notes "Fixes the map." --dry-run

Steps, per buyer: build BOTH flavours - the .exe copy (Build Pro Buyer Copy.py --buyer KEY) and the no-exe copy
(Build Pro Buyer Copy (no exe).py --buyer KEY) - and publish each (publish_update.py --zip ... --key KEY
--flavor exe|noexe), so whichever build the buyer installed gets a matching update that self-applies. Bump
Fleet Manager Pro\VERSION FIRST, or the server won't see the rebuild as newer. The licence server must be running.
"""
import argparse
import csv
import os
import subprocess  # nosec B404  # runs our own build/publish scripts
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
BUILD_OUTPUT = os.path.join(os.path.dirname(DEV), "Build Output")
BUILD_SCRIPT = os.path.join(DEV, "Build Pro Buyer Copy.py")
BUILD_SCRIPT_NOEXE = os.path.join(DEV, "Build Pro Buyer Copy (no exe).py")
# (flavour, build script, buyer zip name, stock zip name) - a buyer may run either build, so ship both.
FLAVORS = (
    ("exe",   BUILD_SCRIPT,       "For Buyer - {key}.zip",          "For Buyer.zip"),
    ("noexe", BUILD_SCRIPT_NOEXE, "For Buyer (no exe) - {key}.zip", "For Buyer (no exe).zip"),
)
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


def run(cmd, cwd):
    print("    $", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    return subprocess.run(cmd, cwd=cwd).returncode  # nosec B603  # fixed argument list, no shell


def _build_one(flavor, script, zip_path, notes, key=None, stock=False):
    if stock:
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


def build_and_publish(key, notes, stock=False):
    """Build and publish BOTH flavours (exe and no-exe) for this buyer, so whichever build they installed
    gets a matching update. Returns None when every flavour published, else a summary of what failed."""
    errs = []
    for flavor, script, buyer_tmpl, stock_name in FLAVORS:
        name = stock_name if stock else buyer_tmpl.format(key=key)
        zip_path = os.path.join(BUILD_OUTPUT, name)
        err = _build_one(flavor, script, zip_path, notes, key=None if stock else key, stock=stock)
        if err:
            errs.append(err)
    return "; ".join(errs) if errs else None


def main():
    ap = argparse.ArgumentParser(description="Build + publish this version to every buyer")
    ap.add_argument("--keys", nargs="*", help="only these purchase keys (default: all in the private ledger)")
    ap.add_argument("--stock", action="store_true", help="also build + publish a stock copy")
    ap.add_argument("--notes", default="", help="release notes shown to every buyer")
    ap.add_argument("--dry-run", action="store_true", help="list what would be built, do nothing")
    ap.add_argument("--yes", action="store_true", help="don't ask before starting")
    a = ap.parse_args()

    ver = version()
    keys = [k.strip().upper() for k in a.keys] if a.keys else buyers_from_csv()
    bad = [k for k in keys if not KEY_RX.match(k)]
    if bad:
        sys.exit(f"Not purchase keys: {bad}")
    if not keys and not a.stock:
        sys.exit(f"No buyers found in {os.path.basename(CSV_PATH)}. Sell a copy first, or pass --keys / --stock.")

    print(f"Version to ship: Fleet Manager Pro {ver}")
    print(f"Buyers to update: {len(keys)}" + ("  (+ stock copy)" if a.stock else ""))
    for k in keys:
        print("  -", k)
    if a.dry_run:
        print("\n(dry run - nothing built)")
        return
    if not a.yes:
        ans = input(f"\nBuild and publish {len(keys) + (1 if a.stock else 0)} copies? Each takes a few minutes. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    results = []
    todo = [(k, False) for k in keys] + ([("STOCK", True)] if a.stock else [])
    for i, (key, stock) in enumerate(todo, 1):
        who = "stock copy" if stock else key
        print(f"\n[{i}/{len(todo)}] {who}")
        try:
            err = build_and_publish(key, a.notes, stock=stock)
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
