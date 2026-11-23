r"""Seller tool: ship ONE new version to EVERYONE in a single run.

Fleet Manager Pro is free now, so there is one build: the public copy (For Everyone). This builds it once at the
current VERSION and publishes the same package to the public update channel AND to every older buyer copy's own
channel (and the stock channel), so every copy out there shows "Update available" - and older buyer copies become
the public build when they install it.

    python "Build and Publish Update (All Buyers).py"                 public channel + every buyer on the server
    python "Build and Publish Update (All Buyers).py" --from-csv      ...buyers from the old Buyer Builds (PRIVATE).csv
    python "Build and Publish Update (All Buyers).py" --only-public   just the public channel
    python "Build and Publish Update (All Buyers).py" --keys FMP-AAAA-... FMP-BBBB-...   public + just these buyers
    python "Build and Publish Update (All Buyers).py" --notes "Fixes the map." --dry-run

--public and --stock are still accepted (the public channel is always published; --stock adds the stock channel).
Bump Fleet Manager Pro\VERSION FIRST, or the server won't see the build as newer.
"""
import argparse
import csv
import os
import subprocess  # nosec B404  # runs our own build/publish scripts
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
BUILD_OUTPUT = os.path.join(os.path.dirname(DEV), "Build Output")
BUILD_SCRIPT = os.path.join(DEV, "Build Pro Buyer Copy.py")
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


def build_public():
    """Build the public copy once. Returns (zip path, None) or (None, what failed)."""
    rc = run([sys.executable, BUILD_SCRIPT], DEV)
    zip_path = os.path.join(BUILD_OUTPUT, PUBLIC_ZIP)
    if rc != 0:
        return None, f"build failed (exit {rc})"
    if not os.path.isfile(zip_path):
        return None, f"the build produced no zip ({zip_path})"
    return zip_path, None


def publish(zip_path, target, notes):
    """target: "--public", "--stock" or a purchase key (that older buyer copy's channel)."""
    cmd = [sys.executable, PUBLISH, "--zip", zip_path, "--flavor", "exe"]
    cmd += [target] if target.startswith("--") else ["--key", target]
    if notes:
        cmd += ["--notes", notes]
    rc = run(cmd, HOST)
    return None if rc == 0 else f"publish failed (exit {rc})"


def main():
    ap = argparse.ArgumentParser(description="Build + publish this version to every buyer")
    ap.add_argument("--keys", nargs="*", help="only these purchase keys (default: every active key on the licence server)")
    ap.add_argument("--from-csv", action="store_true", help="use the old local Buyer Builds (PRIVATE).csv instead of the server")
    ap.add_argument("--stock", action="store_true", help="also publish to the stock channel")
    ap.add_argument("--public", action="store_true", help="(always on - kept so older commands still work)")
    ap.add_argument("--only-public", action="store_true", help="publish only to the public channel")
    ap.add_argument("--notes", default="", help="release notes shown to every buyer")
    ap.add_argument("--dry-run", action="store_true", help="list what would be built, do nothing")
    ap.add_argument("--yes", action="store_true", help="don't ask before starting")
    a = ap.parse_args()

    ver = version()
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

    print(f"Version to ship: Fleet Manager Pro {ver}")
    print(f"Channels: public + {len(keys)} older buyer copies (from {source})" + ("  + stock" if a.stock else ""))
    for k in keys:
        print("  -", k)
    if a.dry_run:
        print("\n(dry run - nothing built)")
        return
    if not a.yes:
        ans = input(f"\nBuild once and publish to {1 + len(keys) + a.stock} channels? It takes a few minutes. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    print("\n[build] public copy")
    zip_path, err = build_public()
    if err:
        sys.exit(f"Nothing was published: {err}")
    results = []
    todo = ["--public"] + (["--stock"] if a.stock else []) + keys
    for i, target in enumerate(todo, 1):
        who = {"--public": "public channel", "--stock": "stock channel"}.get(target, target)
        print(f"\n[{i}/{len(todo)}] {who}")
        try:
            err = publish(zip_path, target, a.notes)
        except Exception as e:  # keep going for the other channels
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
