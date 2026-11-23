"""Developer tool: the games' diesel price in every state / country, added to fmpro/cargo_list.json.

    python tools/build_fuel_prices.py "D:/.../American Truck Simulator" "D:/.../Euro Truck Simulator 2"

Run it again after a game update: SCS changes the prices now and then. The games have no AdBlue price (it is free there).
Only names and prices are stored, per litre, in the game's own currency.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_cargo import read_archive  # noqa: E402

BODY = re.compile(rb"\bcountry_data\s*:\s*country\.data\.(\w+)")
PRICE = re.compile(rb"^\s*fuel_price\s*:\s*([\d.]+)", re.M)
SAME = {"Czech": "Czech Republic", "Uk": "United Kingdom"}          # as the city list spells them


def main(folders):
    out_path = os.path.join(os.path.dirname(HERE), "fmpro", "cargo_list.json")
    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("fuel_prices", {})
    for folder in folders:
        game = "ets2" if "euro" in folder.lower() else "ats"
        found = {}
        for a in sorted(os.listdir(folder)):
            if a == "def.scs" or (a.startswith("dlc_") and a.endswith(".scs")):
                for body in read_archive(os.path.join(folder, a)):
                    m, p = BODY.search(body), PRICE.search(body)
                    if m and p and float(p.group(1)) > 0:
                        key = m.group(1).decode().replace("_", " ").title()
                        found[SAME.get(key, key)] = round(float(p.group(1)), 4)
        data["fuel_prices"][game] = dict(sorted(found.items()))
        listed = {c["country"] for c in data["cities"][game]}
        print(game, len(found), "prices;", "cities without one:", sorted(listed - set(found)))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1:])
