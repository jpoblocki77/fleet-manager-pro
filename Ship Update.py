r"""Ship a new version of Fleet Manager Pro to everyone, in one run (or double-click  Ship Update.bat ).

Fleet Manager Pro is free, so there is one build: the public copy. This
  1. builds it once at the current Fleet Manager Pro\VERSION,
  2. publishes it to the public update channel AND to every older buyer copy's channel (so they move over),
  3. makes the installer (Build Output\Installers\Public\Fleet Manager Pro Setup.exe) and puts a copy in the
     website's downloads folder (..\..\Website\downloads) when that folder is there,
  4. packs  Build Output\updates-for-cloud-<version>-<date>.zip  - everything the cloud server needs.
Then upload that zip's updates\ folder to the server's License Host folder (and the website, for the new download).

    python "Ship Update.py" --notes "Fixes the map."          everything above
    python "Ship Update.py" --dry-run                          just show what it would do
    python "Ship Update.py" --only-public                      skip the older buyer channels
    python "Ship Update.py" --keys FMP-AAAA-... FMP-BBBB-...   public + just these older copies
    python "Ship Update.py" --no-installer                     skip step 3

The older buyer channels come from the Fleet Manager server when License Manager can reach it, otherwise from
Buyer Builds (PRIVATE).csv. Bump Fleet Manager Pro\VERSION FIRST, or nobody is offered the update.
"""
import argparse
import csv
import datetime
import glob
import os
import re
import shutil
import subprocess  # nosec B404  # runs our own build/publish scripts
import sys
import zipfile

DEV = os.path.dirname(os.path.abspath(__file__))
BUILD_OUTPUT = os.path.join(os.path.dirname(DEV), "Build Output")
BUILD_SCRIPT = os.path.join(DEV, "Build Public Copy.py")
INSTALLER_SCRIPT = os.path.join(DEV, "Build Installer.py")
PUBLIC_ZIP = "For Everyone.zip"
HOST = os.path.join(DEV, "Fleet Manager License Host")
UPDATES = os.path.join(HOST, "updates")
PUBLISH = os.path.join(HOST, "publish_update.py")
CSV_PATH = os.path.join(DEV, "Buyer Builds (PRIVATE).csv")
SETUP = os.path.join(BUILD_OUTPUT, "Installers", "Public", "Fleet Manager Pro Setup.exe")
WEBSITE_DOWNLOAD = os.path.join(os.path.dirname(os.path.dirname(DEV)), "Website", "downloads")
KEY_RX = re.compile(r"^FMP-[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$")


def version():
    with open(os.path.join(DEV, "Fleet Manager Pro", "VERSION"), encoding="utf-8") as fh:
        return fh.read().strip()


def buyers_from_csv():
    """Unique purchase keys of the older buyer copies (the private ledger of copies built before 1.5.29)."""
    keys = []
    if not os.path.isfile(CSV_PATH):
        return keys
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            key = (row[1] if len(row) > 1 else "").strip().upper()
            if KEY_RX.match(key) and key not in keys:
                keys.append(key)
    return keys


def buyers_from_server():
    """Active Fleet Manager Pro purchase keys from the server's database (needs License Manager to reach it)."""
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


def channel_tag(target):
    """The updates\\<folder> a target publishes into: "--public" or an older buyer's key."""
    sys.path.insert(0, HOST)
    import publish_update  # noqa: E402
    return publish_update.tag_for_key("PUBLIC" if target == "--public" else target)


def run(cmd, cwd):
    print("    $", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    return subprocess.run(cmd, cwd=cwd).returncode  # nosec B603  # fixed argument list, no shell


def publish(zip_path, target, notes):
    cmd = [sys.executable, PUBLISH, "--zip", zip_path, "--flavor", "exe"]
    cmd += [target] if target.startswith("--") else ["--key", target]
    if notes:
        cmd += ["--notes", notes]
    return None if run(cmd, HOST) == 0 else "publish failed"


def pack_cloud(ver, tags):
    """updates-for-cloud-<v>-<date>.zip: each channel's package and notes, and the "please update" notices."""
    num = re.match(r"[\d.]+", ver).group(0).rstrip(".")
    out = os.path.join(BUILD_OUTPUT, f"updates-for-cloud-{ver}-{datetime.date.today():%Y%m%d}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for t in tags:
            pkg = os.path.join(UPDATES, t, f"Fleet Manager Pro {ver}.zip")
            if not os.path.isfile(pkg):
                raise FileNotFoundError(pkg)
            z.write(pkg, f"updates/{t}/Fleet Manager Pro {ver}.zip")
            for n in glob.glob(os.path.join(UPDATES, t, "notes-*.txt")):
                z.write(n, f"updates/{t}/{os.path.basename(n)}")
        notices = os.path.join(UPDATES, "notices.json")
        if os.path.isfile(notices):
            z.write(notices, "updates/notices.json")
    print(f"    {out}  ({os.path.getsize(out) / 1e6:.1f} MB, {len(tags)} channels, version {num})")
    return out


def main():
    ap = argparse.ArgumentParser(description="Build Fleet Manager Pro once and ship it to everyone")
    ap.add_argument("--keys", nargs="*", help="only these older buyer copies (default: all of them)")
    ap.add_argument("--from-csv", action="store_true", help="take the older buyer copies from Buyer Builds (PRIVATE).csv")
    ap.add_argument("--only-public", action="store_true", help="publish only to the public channel")
    ap.add_argument("--stock", action="store_true", help="also publish to the stock channel")
    ap.add_argument("--public", action="store_true", help="(always on - kept so older commands still work)")
    ap.add_argument("--notes", default="", help="release notes everyone sees (one line)")
    ap.add_argument("--no-installer", action="store_true", help="don't make the installer")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, do nothing")
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
            keys, source = buyers_from_server(), "the Fleet Manager server"
        except Exception as e:  # License Manager is local-only on the cloud box: fall back to the private list
            keys, source = buyers_from_csv(), f"{os.path.basename(CSV_PATH)} (server not reachable from here: {e.__class__.__name__})"
    bad = [k for k in keys if not KEY_RX.match(k)]
    if bad:
        sys.exit(f"Not purchase keys: {bad}")

    todo = ["--public"] + (["--stock"] if a.stock else []) + keys
    print(f"Ship Fleet Manager Pro {ver}")
    print(f"  Channels: public + {len(keys)} older buyer copies (from {source})" + ("  + stock" if a.stock else ""))
    for k in keys:
        print(f"    - {k[:8]}...")
    print("  Then: " + ("the installer, " if not a.no_installer else "") + "the cloud upload zip")
    if a.dry_run:
        print("\n(dry run - nothing done)")
        return
    if not a.yes and input(f"\nBuild once and ship to {len(todo)} channels? It takes about 10 minutes. [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return

    print("\n[1] Build the public copy")
    if run([sys.executable, BUILD_SCRIPT], DEV) != 0 or not os.path.isfile(os.path.join(BUILD_OUTPUT, PUBLIC_ZIP)):
        sys.exit("Nothing was published: the build failed (see above).")
    zip_path = os.path.join(BUILD_OUTPUT, PUBLIC_ZIP)

    print("\n[2] Publish")
    done, failed = [], []
    for i, target in enumerate(todo, 1):
        who = {"--public": "public channel", "--stock": "stock channel"}.get(target, target[:8] + "...")
        print(f"  [{i}/{len(todo)}] {who}")
        try:
            err = publish(zip_path, target, a.notes)
        except Exception as e:  # keep going for the other channels
            err = f"error: {e}"
        if err:
            failed.append((who, err))
        else:
            done.append(target)

    setup_ok = None
    if not a.no_installer:
        print("\n[3] Installer")
        setup_ok = run([sys.executable, INSTALLER_SCRIPT], DEV) == 0 and os.path.isfile(SETUP)
        if setup_ok and os.path.isdir(WEBSITE_DOWNLOAD):
            shutil.copy2(SETUP, os.path.join(WEBSITE_DOWNLOAD, os.path.basename(SETUP)))
            print(f"    copied to the website: {WEBSITE_DOWNLOAD}")

    print("\n[4] Cloud upload zip")
    cloud = None
    try:
        cloud = pack_cloud(ver, [channel_tag(t) for t in done if t != "--stock"])
    except Exception as e:
        failed.append(("cloud zip", f"error: {e}"))

    print("\n" + "=" * 60)
    print(f"  Fleet Manager Pro {ver}: {len(done)} of {len(todo)} channels published.")
    for who, err in failed:
        print(f"  FAIL  {who}  - {err}")
    if setup_ok is False:
        print("  FAIL  installer - see [3] above")
    print("\n  Next:")
    if cloud:
        print(f"   1. Upload {os.path.basename(cloud)} to the cloud server and unzip it into the")
        print("      License Host folder there (it only adds files under updates\\).")
    if setup_ok and os.path.isdir(WEBSITE_DOWNLOAD):
        print("   2. Upload the website's downloads\\Fleet Manager Pro Setup.exe (the new download for new users).")
    print("   3. Commit the release notes (Fleet Manager License Host\\updates\\...\\notes-*.txt) and push.")
    if failed or setup_ok is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
