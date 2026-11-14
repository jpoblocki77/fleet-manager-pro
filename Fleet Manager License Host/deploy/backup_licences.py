"""Nightly safe copy of the licence database (purchases + PCs). Keeps the last 30 days.

install.sh puts this at /usr/local/sbin/fleet-licence-backup.py and runs it every night at 03:00
(/etc/cron.d/fleet-licence-backup). Copies land in /var/backups/fleet-licence/ - download them to your PC now and then.
Run it by hand any time:  sudo python3 /usr/local/sbin/fleet-licence-backup.py
"""
import datetime
import glob
import os
import sqlite3

SRC = "/opt/fleet-licence/licenses.db"
DEST = "/var/backups/fleet-licence"
KEEP_DAYS = 30

os.makedirs(DEST, mode=0o700, exist_ok=True)
out = os.path.join(DEST, f"licenses-{datetime.date.today().isoformat()}.db")
src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
dst = sqlite3.connect(out)
src.backup(dst)  # a consistent copy, even while the server is running
ok = dst.execute("pragma integrity_check").fetchone()[0]
dst.close()
src.close()
os.chmod(out, 0o600)

cutoff = datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)
for old in glob.glob(os.path.join(DEST, "licenses-*.db")):
    try:
        if datetime.date.fromisoformat(os.path.basename(old)[9:19]) < cutoff:
            os.remove(old)
    except ValueError:
        pass
print(f"backup {out}: {ok}")
