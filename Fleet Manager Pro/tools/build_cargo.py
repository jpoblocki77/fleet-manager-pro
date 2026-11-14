"""Developer tool: build the cargo list (fmpro/cargo_list.json) from an installed game.

    python tools/build_cargo.py "D:\\...\\American Truck Simulator" ["D:\\...\\Euro Truck Simulator 2"]

Reads the game's own definition archives (def.scs + dlc_*.scs) for every cargo and locale.scs for its English
name, so the list is complete and spelled exactly as telemetry reports it. Only cargo ids and names are kept.
"""
import json
import os
import re
import struct
import sys
import zlib


def read_archive(path):
    """Yield the bytes of every plain file in an SCS HashFS v2 archive (no path lookup needed)."""
    with open(path, "rb") as f:
        head = f.read(64)
        if head[:4] != b"SCS#" or struct.unpack_from("<H", head, 4)[0] != 2:
            return
        n_ent, ent_c, n_meta, meta_c, ent_off, meta_off = struct.unpack_from("<IIIIQQ", head, 12)
        f.seek(ent_off)
        ent = zlib.decompress(f.read(ent_c))
        f.seek(meta_off)
        meta = zlib.decompress(f.read(meta_c))
        for i in range(n_ent):
            mi, mc = struct.unpack_from("<IH", ent, i * 16 + 8)
            for k in range(mc):
                word = struct.unpack_from("<I", meta, (mi + k) * 4)[0]
                idx, kind = word & 0xFFFFFF, word >> 24
                if kind != 0x80:                       # 0x80 = plain file (0x81 directory, others images...)
                    continue
                csz_flags, size, _unk, off = struct.unpack_from("<IIII", meta, idx * 4)
                csize, comp = csz_flags & 0xFFFFFF, csz_flags >> 28
                size &= 0xFFFFFF
                if size > 4_000_000 or csize == 0:
                    continue
                f.seek(off * 16)
                raw = f.read(csize)
                try:
                    yield zlib.decompress(raw) if comp == 1 else (raw if comp == 0 else b"")
                except zlib.error:
                    continue


CARGO_RX = re.compile(rb"cargo_data\s*:\s*cargo\.([a-z0-9_]+)\s*\{(.*?)\n\}", re.S)
NAME_RX = re.compile(rb'\bname\s*:\s*"([^"]*)"')
KEY_RX = re.compile(rb'key\[\]\s*:\s*"([^"]+)"\s*val\[\]\s*:\s*"((?:[^"\\]|\\.)*)"')


ENGLISH_WORDS = (" and ", " of ", "empty", "used", "parts", "frozen", "wood", "steel", "tank", "machine", "truck",
                 "trailer", "pipes", "oil", "food", "loader", "tractor", "excavator", "water", "glass", "boxes", "tires")
BRITISH_WORDS = ("aluminium", "tyre", "colour", "fibre", "litre", "sulphur", "yoghurt", "lorry")


COMPANY_RX = re.compile(rb"company_permanent\s*:\s*company\.permanent\.([a-z0-9_]+)\s*\{(.*?)\n\}", re.S)


CITY_RX = re.compile(rb"city_data\s*:\s*city\.([a-z0-9_]+)\s*\{(.*?)\n\}", re.S)
# English city names that differ from the native one: used to pick the English language file
CITY_ANCHORS = {"wien": "Vienna", "koln": "Cologne", "praha": "Prague", "warszawa": "Warsaw", "roma": "Rome",
                "munchen": "Munich", "lisboa": "Lisbon", "kobenhavn": "Copenhagen", "milano": "Milan",
                "napoli": "Naples", "torino": "Turin", "firenze": "Florence", "venezia": "Venice", "genova": "Genoa",
                "sevilla": "Seville", "bucuresti": "Bucharest", "beograd": "Belgrade", "athina": "Athens",
                "bruxelles": "Brussels", "geneve": "Geneva", "goteborg": "Gothenburg", "moskva": "Moscow"}


def _field(body, key):
    m = re.search(rb"\b" + key + rb'\s*:\s*"?([^"\r\n]*)"?', body)
    return m.group(1).decode("utf-8", "replace").strip() if m else ""


def scan_game(folder):
    cargo, companies, cities = {}, {}, {}
    archives = sorted(a for a in os.listdir(folder) if a.endswith(".scs") and (a == "def.scs" or a.startswith("dlc_")))
    for a in archives:
        for data in read_archive(os.path.join(folder, a)):
            if b"city_data" in data:
                for m in CITY_RX.finditer(data):
                    body = m.group(2)
                    if _field(body, b"city_name"):
                        cities.setdefault(m.group(1).decode(), (_field(body, b"city_name"),
                                                                 _field(body, b"city_name_localized"), _field(body, b"country")))
            if b"company_permanent" in data:
                for m in COMPANY_RX.finditer(data):
                    nm = NAME_RX.search(m.group(2))
                    if nm and nm.group(1):
                        companies.setdefault(m.group(1).decode(), nm.group(1).decode("utf-8", "replace"))
            if b"cargo_data" not in data:
                continue
            for m in CARGO_RX.finditer(data):
                nm = NAME_RX.search(m.group(2))
                cargo.setdefault(m.group(1).decode(), nm.group(1).decode("utf-8", "replace") if nm else "")
    # English names: the locale file whose keys resolve to English text
    best, groups = {}, {}
    keys_needed = {v.strip("@") for v in cargo.values() if v.startswith("@@")}
    keys_needed |= {c[1].strip("@") for c in cities.values() if c[1].startswith("@@")}
    loc = os.path.join(folder, "locale.scs")
    for data in (read_archive(loc) if os.path.exists(loc) else ()):
        if b"key[]" not in data:
            continue
        d = {k.decode(): v.decode("utf-8", "replace") for k, v in KEY_RX.findall(data)}
        hit = frozenset(keys_needed & set(d))
        if not hit:
            continue
        # The same set of keys exists once per language (and the names are spread over several files:
        # base game, each DLC...). For every such set keep the US-English file.
        text = " " + " ".join(d[k] for k in hit).lower() + " "
        score = sum(text.count(w) for w in ENGLISH_WORDS) + (1000 if text.isascii() else 0) \
            - 3 * sum(text.count(w) for w in BRITISH_WORDS) \
            + 500 * sum(1 for k, en in CITY_ANCHORS.items() if d.get(k) == en)
        if score > groups.get(hit, ({}, -10 ** 9))[1]:
            groups[hit] = (d, score)
    for d, _score in groups.values():
        best.update({k: v for k, v in d.items() if k in keys_needed})
    out = {}
    for cid, nm in cargo.items():
        name = best.get(nm.strip("@"), "") if nm.startswith("@@") else nm
        if name:
            out[cid] = name
    places = []
    for cid, (native, key, country) in cities.items():
        name = (best.get(key.strip("@")) if key.startswith("@@") else "") or native
        country = country.replace("_", " ").title()
        country = {"Uk": "United Kingdom", "Czech": "Czech Republic"}.get(country, country)
        if country in ("", "Halloween"):          # seasonal event places are not real destinations
            continue
        places.append({"id": cid, "name": name, "native": native if native != name else "", "country": country})
    places.sort(key=lambda c: (c["name"].lower(), c["country"]))
    return out, companies, places, len(cargo), len(archives)


def listed(d):
    return [{"id": k, "name": v} for k, v in sorted(d.items(), key=lambda kv: (kv[1].lower(), kv[0]))]


def fallback(folder, skip_cargo, skip_companies):
    """Stand-in lists for a game that isn't installed: TS SE Tool's lang\\Default folder (Apache License 2.0)."""
    def read(name, skip):
        out = {}
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            return out
        for line in open(path, encoding="utf-8-sig"):
            if ";" not in line:
                continue
            cid, nm = (x.strip() for x in line.split(";", 1))
            if nm and "_" not in nm and re.fullmatch(r"[a-z0-9_]+", cid) and cid not in skip:
                out[cid] = nm
        return out
    return read("cargo_translate.txt", skip_cargo), read("companies_translate.txt", skip_companies)


if __name__ == "__main__":
    args = sys.argv[1:]
    fb = None
    if "--fallback" in args:
        i = args.index("--fallback")
        fb = args[i + 1]
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    from depots import depots
    result = {"companies": {}, "cities": {}, "depots": {}, "_sources": {}}
    for folder in args:
        game = "ets2" if "euro" in folder.lower() else "ats"
        names, companies, places, found, n_arch = scan_game(folder)
        result[game], result["companies"][game], result["cities"][game] = listed(names), listed(companies), places
        result["_sources"][game] = "read from the game's own definition files (tools/build_cargo.py)"
        known = {c["id"] for c in places}
        result["depots"][game] = {c: v for c, v in depots(folder, list(companies)).items() if c in known}
        print(f"{game}: {n_arch} archives, {found} cargo definitions, {len(set(names.values()))} cargo names, "
              f"{len(companies)} companies, {len(set(companies.values()))} company names, {len(places)} cities, "
              f"{sum(map(len, result['depots'][game].values()))} depots in {len(result['depots'][game])} cities")
    for game in ("ats", "ets2"):
        if game in result or not fb:
            continue
        other = "ets2" if game == "ats" else "ats"
        cargo, companies = fallback(fb, {x["id"] for x in result.get(other, [])},
                                    {x["id"] for x in result["companies"].get(other, [])})
        result[game], result["companies"][game] = listed(cargo), listed(companies)
        result["_sources"][game] = "community list from TS SE Tool (Apache License 2.0); re-run with the game installed"
        print(f"{game}: stand-in list, {len(set(cargo.values()))} cargo names, {len(set(companies.values()))} company names")
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), "fmpro", "cargo_list.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("written:", out, os.path.getsize(out) // 1024, "KB")
