"""Developer tool: dealer prices of every truck and ownable trailer, added to fmpro/cargo_list.json.

    python tools/build_vehicles.py "D:\\...\\American Truck Simulator" ["D:\\...\\Euro Truck Simulator 2"]

A truck's price in the game is the sum of its parts. For each truck this reads def/vehicle/truck/<id>/:
  * standard: the dealer's default build (the chassis/cabin/engine/transmission named in data.sii's fallback[] list)
  * from:     the cheapest chassis + cabin + engine + transmission
Owned trailers (def/vehicle/trailer_owned/<id>/): cheapest chassis + cheapest body. Paint and accessories are extra.
Only ids, names and prices are stored.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import depots  # noqa: E402

PRICE = re.compile(rb"\bprice\s*:\s*(\d+)")
FALLBACK = re.compile(rb'fallback\[\]\s*:\s*"(\w+)\|([^"|]+)"')
TRUCK_PARTS = ("chassis", "cabin", "engine", "transmission")
BRANDS = {"intnational": "International", "westernstar": "Western Star", "mercedes": "Mercedes-Benz", "daf": "DAF",
          "man": "MAN", "kenworth": "Kenworth", "peterbilt": "Peterbilt", "freightliner": "Freightliner", "mack": "Mack",
          "volvo": "Volvo", "scania": "Scania", "iveco": "Iveco", "renault": "Renault", "lodeking": "Lode King",
          "prestige": "Prestige", "krone": "Krone", "schwarzmuller": "Schwarzmüller", "feldbinder": "Feldbinder",
          "kassbohrer": "Kässbohrer", "tirsan": "Tirsan", "wielton": "Wielton", "fruehauf": "Fruehauf",
          "wilson": "Wilson", "hyundai": "Hyundai", "utility": "Utility", "greatdane": "Great Dane",
          "wabash": "Wabash", "trailking": "Trail King", "fontaine": "Fontaine", "ironhorse": "Ironhorse",
          "scs": "SCS", "schwmuller": "Schwarzmüller", "schmitz": "Schmitz Cargobull", "kogel": "Kögel"}


def pretty_model(m, truck=False):
    out = []
    for p in re.split(r"[_]+", m):
        for q in re.sub(r"(?<=[a-z])(?=\d{4}$)", " ", p).split():        # cascadia2019 -> cascadia 2019
            if re.fullmatch(r"\d+i", q):
                out.append(q)                                              # 9900i
            elif re.search(r"\d", q) and len(q) <= 6:
                out.append(q.upper().replace("EURO6", "Euro 6"))
            elif truck and len(q) <= 3:
                out.append(q.upper())                                      # vnl -> VNL, tgx -> TGX, fh -> FH
            else:
                out.append(q.title())
    return " ".join(out).replace("Etech", "E-Tech")


def name_of(vid, truck=False):
    brand, _, model = vid.partition(".")
    return BRANDS.get(brand, brand.title()), pretty_model(model, truck)


class Game:
    def __init__(self, folder):
        self.arcs = [a for a in (depots.Archive(os.path.join(folder, f)) for f in sorted(os.listdir(folder))
                                 if f.endswith(".scs") and (f == "def.scs" or f.startswith("dlc_"))) if a.ok]

    def listdir(self, path):
        out = set()
        for a in self.arcs:
            out |= set(a.listdir(path))
        return sorted(out)

    def read(self, path):
        for a in reversed(self.arcs):                      # DLC files override the base game
            b = a.read(path)
            if b:
                return b
        return b""

    def prices(self, path):
        out = {}
        for n in self.listdir(path):
            if n.endswith(".sii"):
                m = PRICE.search(self.read(f"{path}/{n}"))
                if m and int(m.group(1)) > 0:
                    out[n] = int(m.group(1))
        return out


def scan(folder):
    g = Game(folder)
    trucks, trailers = [], []
    for d in g.listdir("def/vehicle/truck"):
        if not d.startswith("/"):
            continue
        vid = d.strip("/")
        base = f"def/vehicle/truck/{vid}"
        parts = {p: g.prices(f"{base}/{p}") for p in TRUCK_PARTS}
        if not all(parts.values()):
            continue
        cheapest = sum(min(v.values()) for v in parts.values())
        std = {k.decode(): v.decode() for k, v in FALLBACK.findall(g.read(f"{base}/data.sii"))}
        standard = sum(parts[p].get(std.get(p, ""), min(parts[p].values())) for p in TRUCK_PARTS)
        brand, model = name_of(vid, truck=True)
        trucks.append({"id": vid, "brand": brand, "model": model, "price": standard, "from": cheapest})
    for d in g.listdir("def/vehicle/trailer_owned"):
        if not d.startswith("/"):
            continue
        vid = d.strip("/")
        base = f"def/vehicle/trailer_owned/{vid}"
        chassis, body = g.prices(f"{base}/chassis"), g.prices(f"{base}/body")
        if not chassis:
            continue
        brand, model = name_of(vid)
        trailers.append({"id": vid, "brand": brand, "model": model,
                         "from": min(chassis.values()) + (min(body.values()) if body else 0)})
    for a in g.arcs:
        a.close()
    return sorted(trucks, key=lambda t: (t["brand"], t["model"])), sorted(trailers, key=lambda t: (t["brand"], t["model"]))


BODY_RX = re.compile(rb"body_types?\[?\]?\s*:\s*(\w+)")
BODY_LABELS = {"dryvan": "Dry van", "refrigerated": "Refrigerated (reefer)", "insulated": "Insulated",
               "curtainside": "Curtainside", "flatbed": "Flatbed", "flatbed_brck": "Flatbed (bricks)",
               "flatbed_cont": "Flatbed (containers)", "dropdeck": "Drop deck / step deck", "lowboy": "Lowboy",
               "lowbed": "Lowbed", "container": "Container chassis", "dumper": "Dump trailer", "hopper": "Grain hopper",
               "bulkfeed": "Bulk feed", "silo": "Silo / dry bulk", "chipvan": "Chip van", "log": "Log trailer",
               "livestock": "Livestock", "foodtank": "Food-grade tanker", "fueltank": "Fuel tanker",
               "gastank": "Gas tanker", "chemtank": "Chemical tanker"}
SPECIAL = {"_car_": ("car_transporter", "Car transporter"), "_oversize": ("oversize", "Oversize / special transport"),
           "_gls_": ("glass", "Glass inloader"), "_lowboat": ("boat", "Boat transporter"), "_tran": ("heavy_haul", "Heavy haul (multi-axle)")}


def trailer_types(folder):
    """Every trailer body the game allows: the standard bodies cargo can go in, plus special transporters."""
    import build_cargo
    found = set()
    for a in sorted(f for f in os.listdir(folder) if f.endswith(".scs") and (f == "def.scs" or f.startswith("dlc_"))):
        for data in build_cargo.read_archive(os.path.join(folder, a)):
            if b"body_type" in data:
                found.update(m.decode() for m in BODY_RX.findall(data))
    out = {b: BODY_LABELS.get(b, b.replace("_", " ").capitalize()) for b in found if not b.startswith("_")}
    for prefix, (bid, label) in SPECIAL.items():
        if any(b.startswith(prefix) for b in found):
            out[bid] = label
    return sorted(([k, v] for k, v in out.items()), key=lambda kv: kv[1].lower())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out_path = os.path.join(os.path.dirname(HERE), "fmpro", "cargo_list.json")
    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("vehicles", {})
    for folder in sys.argv[1:]:
        game = "ets2" if "euro" in folder.lower() else "ats"
        trucks, trailers = scan(folder)
        types = trailer_types(folder)
        data["vehicles"][game] = {"trucks": trucks, "trailers": trailers, "trailer_types": types}
        print(f"{game}: {len(trucks)} trucks, {len(trailers)} ownable trailers, {len(types)} trailer types")
        for t in trucks[:4]:
            print(f"   {t['brand']} {t['model']}: standard {t['price']:,}, from {t['from']:,}")
        for t in trailers[:3]:
            print(f"   trailer {t['brand']} {t['model']}: from {t['from']:,}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("written:", out_path)
