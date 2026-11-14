"""Developer tool: add every garage location of ATS and ETS2 to fmpro/cargo_list.json.

    python tools/build_garages.py [path\\to\\game.sii ...]

Each save lists all of the map's garages (`garage : garage.<city> { ... }`), owned or not. With no arguments
the newest save of every profile on this PC is read (Documents\\American Truck Simulator, ...\\Euro Truck
Simulator 2); the union over all saves is kept, so map DLCs from any profile count. Only city ids are stored.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fmpro import savegame  # noqa: E402

GARAGE_RX = re.compile(r"\ngarage : garage\.(\w+) \{")


def garages_in(path):
    return set(GARAGE_RX.findall(savegame.read_sii(path)))


def main(paths):
    out_path = os.path.join(os.path.dirname(HERE), "fmpro", "cargo_list.json")
    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)
    found = {"ats": set(), "ets2": set()}
    if paths:
        for p in paths:
            game = "ets2" if "euro" in p.lower() else "ats"
            found[game] |= garages_in(p)
    else:
        for game in ("ats", "ets2"):
            for prof in savegame.list_profiles(game):
                try:
                    found[game] |= garages_in(os.path.join(prof["saves"][0]["dir"], "game.sii"))
                except (OSError, savegame.SaveError) as e:
                    print(f"  skipped {prof['name']}: {e}")
    data.setdefault("garages", {})
    for game, ids in found.items():
        known = {c["id"] for c in data.get("cities", {}).get(game, [])}
        keep = sorted(i for i in ids if i in known)
        if keep:
            data["garages"][game] = sorted(set(data["garages"].get(game, [])) | set(keep))
        print(f"{game}: {len(ids)} garages in saves, {len(keep)} matched to the city list, "
              f"{len(data['garages'].get(game, []))} stored")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("written:", out_path)


if __name__ == "__main__":
    main(sys.argv[1:])
