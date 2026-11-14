"""Makes deploy\\upload\\fleet-licence - exactly the files the Kamatera server needs, ready to upload.

Run "Prepare Upload.bat" (or: python prepare_upload.py). It takes a safe copy of licenses.db (fine even while
License Server is running) and gives the Linux files Unix line endings.
"""
import os
import shutil
import sqlite3
import sys

DEPLOY = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(DEPLOY)
OUT = os.path.join(DEPLOY, "upload", "fleet-licence")

PROGRAM = ["license_server.py", "ed25519.py"]
SECRETS = ["host_key.json", "server_config.json"]
LINUX = ["install.sh", "fleet-licence.service", "Caddyfile", "backup_licences.py"]


def main():
    missing = [f for f in PROGRAM + SECRETS if not os.path.exists(os.path.join(HOST, f))]
    if missing:
        sys.exit("Missing from the License Host folder: " + ", ".join(missing) + "  (run License Manager -> Set up first)")
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    for f in PROGRAM + SECRETS:
        shutil.copy2(os.path.join(HOST, f), OUT)
    for f in LINUX:  # Linux needs LF line endings
        with open(os.path.join(DEPLOY, f), "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")
        with open(os.path.join(OUT, f), "wb") as fh:
            fh.write(data)
    purchases = 0
    db = os.path.join(HOST, "licenses.db")
    if os.path.exists(db):
        src, dst = sqlite3.connect(db), sqlite3.connect(os.path.join(OUT, "licenses.db"))
        src.backup(dst)
        if dst.execute("pragma integrity_check").fetchone()[0] != "ok":
            sys.exit("licenses.db failed its integrity check - nothing to upload yet.")
        purchases = dst.execute("select count(*) from purchases").fetchone()[0]
        dst.close()
        src.close()
    print(f"Ready: {OUT}")
    print(f"  {len(os.listdir(OUT))} files, {purchases} purchase(s) in licenses.db\n")
    print("Next:")
    print("  1. Upload the whole 'fleet-licence' folder to /root/ on the server (WinSCP).")
    print("  2. On the server:  sudo bash /root/fleet-licence/install.sh licence.YOURDOMAIN.com")
    print("\nThis folder holds a copy of your PRIVATE signing key. Delete deploy\\upload after uploading.")


if __name__ == "__main__":
    main()
