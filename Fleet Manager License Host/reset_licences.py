"""Clear the licence server's purchase records and start empty - after saving a dated backup.

    python reset_licences.py            (asks you to type RESET)

Deletes every purchase key, every activated PC, and every company Apply page / waiting application. The signing key
(host_key.json) and server_config.json are NOT touched, so copies keep trusting this server - they just need a new key.
Stop License Server first, then start it again afterwards.
"""
import datetime as dt
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "licenses.db")


def main():
    if not os.path.exists(DB):
        sys.exit(f"No licenses.db in {HERE} - nothing to clear.")
    con = sqlite3.connect(DB)
    tables = [t for t in ("purchases", "activations", "recruit", "applications")
              if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    print("This will DELETE from", DB)
    for t, n in counts.items():
        print(f"   {t:<13} {n} record(s)")
    cols = {c[1] for c in con.execute("PRAGMA table_info(purchases)")} if "purchases" in tables else set()
    prod = "COALESCE(product,'Fleet Manager')" if "product" in cols else "'Fleet Manager'"
    for r in (con.execute(f"SELECT key, name, {prod} FROM purchases ORDER BY created") if cols else []):  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
        print(f"      {r[2]:<17} {r[0]}  {r[1]}")
    if input("\nType RESET to clear them (a backup is saved first): ").strip().lstrip("\ufeff").strip() != "RESET":
        sys.exit("Nothing was changed.")
    backup = os.path.join(HERE, f"licenses-backup-before-reset-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
    dst = sqlite3.connect(backup)
    con.backup(dst)
    dst.close()
    with con:
        for t in tables:
            con.execute(f"DELETE FROM {t}")  # nosec B608  # table/column names are fixed in the code; every value is a ? parameter
    con.execute("VACUUM")
    con.close()
    print(f"\nCleared. Backup saved: {backup}")
    print("Start License Server again, then make new Fleet Manager Pro keys in License Manager (one for yourself too).")


if __name__ == "__main__":
    main()
