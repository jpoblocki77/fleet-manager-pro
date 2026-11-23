r"""Seller tool: tell copies on a bad version to update ("please update" notice).

The notices live in updates\notices.json next to the server. Every copy sends its version when it checks for
updates (at start-up and once a day); a copy on a listed version shows the message as a red banner on every page,
with "Update now" for the Owner and Managers. Nothing is locked or switched off: it is only a message.

    python update_notice.py                                   list the notices
    python update_notice.py 1.5.30-beta "Jobs can be paid twice in this version. Please update now."
    python update_notice.py "<1.5.30" "This version is too old for the online features. Please update."
    python update_notice.py --clear 1.5.30-beta               remove one
    python update_notice.py --clear-all                       remove them all

A version on its own matches that version (1.5.30 and 1.5.30-beta are the same). "<1.5.30" matches every version
older than 1.5.30; an exact version wins over a "<" entry. Messages are plain text, at most 300 characters.
The server re-reads the file when it changes, so there's no restart. For the cloud server, upload
updates\notices.json to the same place there (it goes in the updates-for-cloud zip too).
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "updates", "notices.json")
KEY_RX = re.compile(r"^<?\d+(\.\d+)+([-.][0-9A-Za-z.-]+)?$")
MAX = 300


def load():
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        sys.exit(f"{PATH} can't be read ({e}). Fix or delete it first.")


def save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, PATH)


def show(d):
    if not d:
        print("No notices: no copy is asked to update.")
        return
    for k, v in d.items():
        print(f"  {k:<16} {v}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add or remove a 'please update' notice for a version")
    ap.add_argument("version", nargs="?", help='a version like 1.5.30-beta, or "<1.5.30" for every older version')
    ap.add_argument("message", nargs="?", help="what copies on that version are told (plain text)")
    ap.add_argument("--clear", metavar="VERSION", help="remove the notice for this version")
    ap.add_argument("--clear-all", action="store_true", help="remove every notice")
    a = ap.parse_args(argv)
    if a.clear_all:                       # works even when the file is broken
        save({})
        print("All notices removed.")
        return
    d = load()
    if a.clear:
        key = a.clear.strip()
        if key not in d:
            sys.exit(f"There's no notice for {key}.")
        del d[key]
        save(d)
        print(f"Removed the notice for {key}.")
        show(d)
        return
    if not a.version:
        show(d)
        return
    key = a.version.strip()
    if not KEY_RX.match(key):
        sys.exit('A version looks like 1.5.30-beta, or "<1.5.30" for every version older than it.')
    msg = " ".join(str(a.message or "").split())
    if not msg:
        sys.exit("Give the message too, in quotes.")
    if len(msg) > MAX:
        sys.exit(f"Keep the message to {MAX} characters (it's {len(msg)}).")
    d[key] = msg
    save(d)
    print(f"Copies on {key} will be asked to update at their next check.")
    show(d)


if __name__ == "__main__":
    main()
