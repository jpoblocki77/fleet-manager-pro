"""Put dispatch contracts into the game's freight market by writing a new save slot.

How it works (the same idea as Virtual Speditor and the save editors, nothing is hooked into the running game):
  1. read the driver's latest save (ATS/ETS2 encrypt saves: they are decrypted here, read-only);
  2. add one freight-market offer at the pickup company: target company/city, cargo, and the trailer, truck and
     load size copied from an offer for the same cargo that the game itself generated, so the trailer always fits;
  3. write everything as a NEW save slot named "Fleet Manager Pro - Contract #N" (plain text, which the game
     loads). The driver's own saves are never modified.
The driver loads that slot, picks the job up at the pickup company, and the delivery completes the contract.
Single-player only: TruckersMP does not use the player's saves.
"""
import json
import math
import os
import re
import shutil
import struct
import time
import zlib

GAME_FOLDERS = {"ats": "American Truck Simulator", "ets2": "Euro Truck Simulator 2"}
MAP_SCALE = {"ats": 20.0, "ets2": 19.0}         # game world metres x scale = real metres
ROAD_FACTOR = 1.26                              # game route km / straight-line real km (measured on a real save)
SLOT_PREFIX = "fmp_contract_"          # 0.01 builds: the game's Load menu ignores these folders
TITLE_PREFIX = "Fleet Manager Pro - Contract #"


class SaveError(Exception):
    """A problem the user can act on (shown as-is)."""


# ------------------------------------------------------------------------------------------- reading saves
def _key():
    # The games' save encryption key, published by the community save tools (SII Decrypt, TS SE Tool).
    return bytes.fromhex("2a5fcb1791d22fb60245b3d8369ed0b2c27371563fbf1f3c9edf6b11825a5d0a")


def read_sii(path):
    """Text of a .sii file: plain text as-is, encrypted ("ScsC") decrypted. Binary saves are not supported."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        hint = (" It's in OneDrive and may be online-only: in File Explorer right-click the game's profiles folder, "
                "choose \"Always keep on this device\", then try again.") if "onedrive" in path.lower() else ""
        raise SaveError(f"Couldn't read the save ({e.strerror or e}).{hint}") from e
    if raw[:4] == b"ScsC":
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        if len(raw) < 56:
            raise SaveError("The save file is damaged.")
        dec = Cipher(algorithms.AES(_key()), modes.CBC(raw[36:52])).decryptor()
        data = dec.update(raw[56:]) + dec.finalize()
        try:
            raw = zlib.decompress(data)
        except zlib.error:
            raise SaveError("The save file couldn't be decrypted (unexpected format).")
    if raw[:4] == b"BSII":
        raise SaveError("This save is in the game's binary format. In the game console type  g_save_format 2  "
                        "then save again (or set it in config.cfg).")
    if raw[:8] != b"SiiNunit":
        raise SaveError("That doesn't look like a game save.")
    return raw.decode("utf-8", "replace")


# ------------------------------------------------------------------------------------------- finding profiles
def documents_dirs():
    home = os.path.expanduser("~")
    out = [os.path.join(home, "Documents"), os.path.join(home, "OneDrive", "Documents")]
    return [d for d in out if os.path.isdir(d)]


def _profile_name(folder):
    """Profile folders are named with the hex of the profile name ("4a2e4d..." -> "J.McCoy ...")."""
    base = os.path.basename(folder.rstrip("\\/"))
    try:
        name = bytes.fromhex(base).decode("utf-8")
        if name.isprintable():
            return name
    except ValueError:
        pass
    return base


def list_profiles(game, base_dirs=None):
    """Profiles of one game on this PC, newest save first: [{dir, name, saves:[{dir, name, time}]}]."""
    out = []
    for docs in base_dirs or documents_dirs():
        root = os.path.join(docs, GAME_FOLDERS[game])
        for kind in ("profiles", "steam_profiles"):
            pdir = os.path.join(root, kind)
            if not os.path.isdir(pdir):
                continue
            for p in sorted(os.listdir(pdir)):
                full = os.path.join(pdir, p)
                saves = []
                sdir = os.path.join(full, "save")
                if os.path.isdir(sdir):
                    for s in os.listdir(sdir):
                        g = os.path.join(sdir, s, "game.sii")
                        if os.path.isfile(g) and not s.startswith(SLOT_PREFIX) and not s.endswith(".tmp"):
                            name = _slot_name(os.path.join(sdir, s))
                            if not name.startswith(TITLE_PREFIX):          # this app's own slots aren't starting points
                                saves.append({"dir": os.path.join(sdir, s), "slot": s, "name": name,
                                              "time": os.path.getmtime(g)})
                saves.sort(key=lambda x: -x["time"])
                if saves:
                    out.append({"dir": full, "name": _profile_name(full), "steam": kind == "steam_profiles",
                                "saves": saves, "latest": saves[0]["time"]})
    out.sort(key=lambda p: -p["latest"])
    return out


def _slot_name(save_dir):
    try:
        m = re.search(r'\bname: "((?:[^"\\]|\\.)*)"', read_sii(os.path.join(save_dir, "info.sii")))
        if m and m.group(1):
            return m.group(1)
    except (OSError, SaveError):
        pass
    s = os.path.basename(save_dir)
    return {"autosave": "Autosave", "autosave_job": "Job autosave", "quicksave": "Quicksave"}.get(s, s)


# ------------------------------------------------------------------------------------------- the save's contents
_OFFER_RX = re.compile(r"job_offer_data : (\S+) \{\r?\n(.*?)\r?\n\}", re.S)
_COMPANY_RX = re.compile(r"company : company\.volatile\.(\w+)\.(\w+) \{\r?\n(.*?)\r?\n\}", re.S)


def _fields(body):
    return dict(line.strip().split(": ", 1) for line in body.splitlines() if ": " in line)


class Save:
    def __init__(self, text):
        self.text = text
        self.nl = "\r\n" if "\r\n" in text[:4000] else "\n"
        m = re.search(r"\bgame_time: (\d+)", text)
        if not m:
            raise SaveError("The save has no game clock - is it a real save?")
        self.game_time = int(m.group(1))
        self.offers = {n: _fields(b) for n, b in _OFFER_RX.findall(text)}
        self.companies = {}              # (company id, city id) -> [offer names]
        for cid, city, body in _COMPANY_RX.findall(text):
            self.companies[(cid, city)] = re.findall(r"job_offer\[\d+\]: (\S+)", body)
        self.owner = {o: k for k, lst in self.companies.items() for o in lst}

    def real_offers(self):
        return {n: o for n, o in self.offers.items() if o.get("target", '""') != '""' and o.get("cargo", "null") != "null"}


# ------------------------------------------------------------------------------------------- building the offer
def _game_km(game, a, b, cities_xy):
    """Estimated in-game route km between two city names, from the Fleet Map's city positions."""
    pa, pb = cities_xy.get(a.lower()), cities_xy.get(b.lower())
    if not pa or not pb:
        return None
    straight_real_km = math.hypot(pa[0] - pb[0], pa[1] - pb[1]) * MAP_SCALE[game] / 1000
    return int(round(straight_real_km * ROAD_FACTOR))


def plan_offer(save, game, contract, lists, cities_xy):
    """Work out the offer to add. Returns a dict, or raises SaveError with what the dispatcher should change."""
    city_names = {c["id"]: c["name"] for c in lists.get("cities", {}).get(game, [])}
    company_ids = {}
    for c in lists.get("companies", {}).get(game, []):
        company_ids.setdefault(c["name"].lower(), set()).add(c["id"])
    cargo_ids = set()
    for c in lists.get(game, []):
        if c["name"].lower() == (contract.get("cargo") or "").lower():
            cargo_ids.add(c["id"])
    if contract.get("cargo_id"):
        cargo_ids = {contract["cargo_id"]} | cargo_ids

    def resolve(cid, name, company):
        """City id from the contract, else from its name: several cities can share one (Kansas City, KS / MO),
        so keep the ones in this save that have the chosen company."""
        if cid or not name:
            return cid
        ids = [c["id"] for c in lists.get("cities", {}).get(game, []) if c["name"].lower() == name.lower()]
        ids = [i for i in ids if any(k[1] == i for k in save.companies)]
        if company and len(ids) > 1:
            ids = [i for i in ids if any(k[1] == i and k[0] in want(company) for k in save.companies)] or ids
        if len(ids) > 1:
            places = " or ".join(f"{name}, {c['country']}" for c in lists["cities"][game] if c["id"] in ids)
            raise SaveError(f"There is more than one {name} ({places}). Edit the contract and pick the right one.")
        return ids[0] if ids else ""

    def want(name):
        return company_ids.get((name or "").lower(), set())

    src_city = resolve(contract.get("source_city_id") or "", contract.get("source_city") or "", contract.get("source_company"))
    dst_city = resolve(contract.get("destination_city_id") or "", contract.get("destination_city") or "",
                       contract.get("destination_company"))
    if not src_city:
        raise SaveError("Pick a From city from the list (the job has to be offered somewhere).")
    if not dst_city:
        raise SaveError("Pick the To city from the list.")
    if not cargo_ids:
        raise SaveError("Pick a cargo from the list (the game needs to know what to load).")

    real = save.real_offers()
    with_cargo = {n: o for n, o in real.items() if o["cargo"].split(".", 1)[-1] in cargo_ids}
    if not with_cargo:
        raise SaveError(f"This save has no {contract.get('cargo')} jobs anywhere, so the trailer it needs is unknown. "
                        "Pick a different cargo, or drive a little and save again.")

    # pickup company: the chosen one in the From city, else a company there that already ships this cargo
    src_options = [k for k in save.companies if k[1] == src_city]
    if not src_options:
        raise SaveError(f"{city_names.get(src_city, src_city)} isn't in this save (DLC not owned or not yet visited?).")
    if want(contract.get("source_company")):
        src_options = [k for k in src_options if k[0] in want(contract["source_company"])]
        if not src_options:
            raise SaveError(f"{contract['source_company']} has no depot in {city_names.get(src_city, src_city)}.")
    ships = [k for k in src_options if any(save.owner.get(n) == k for n in with_cargo)]
    src = (ships or src_options)[0]

    # delivery company: the chosen one in the To city, else one that receives this cargo there, else any
    dst_options = [k for k in save.companies if k[1] == dst_city]
    if not dst_options:
        raise SaveError(f"{city_names.get(dst_city, dst_city)} isn't in this save (DLC not owned?).")
    if want(contract.get("destination_company")):
        dst_options = [k for k in dst_options if k[0] in want(contract["destination_company"])]
        if not dst_options:
            raise SaveError(f"{contract['destination_company']} has no depot in {city_names.get(dst_city, dst_city)}.")
    receives = {o["target"].strip('"') for o in with_cargo.values()}
    dst = next((k for k in dst_options if f"{k[0]}.{k[1]}" in receives), dst_options[0])
    if dst == src:
        raise SaveError("The pickup and delivery company are the same.")

    # trailer, truck and load size: from an offer for this cargo, preferably at the pickup company itself
    tpl_name = next((n for n in with_cargo if save.owner.get(n) == src), next(iter(with_cargo)))
    tpl = dict(with_cargo[tpl_name])

    # distance: an existing offer between the same two cities, else an estimate
    km = next((int(o["shortest_distance_km"]) for n, o in real.items()
               if save.owner.get(n, ("", ""))[1] == src_city and o["target"].strip('"').endswith("." + dst_city)
               and o.get("shortest_distance_km", "0").isdigit() and int(o["shortest_distance_km"]) > 0), None)
    if km is None:
        km = _game_km(game, city_names.get(src_city, ""), city_names.get(dst_city, ""), cities_xy) or 0
    urgency = {"urgent": "2", "high": "1"}.get(contract.get("priority"), "0")
    # Every depot has a fixed set of job slots. A job added as an extra slot loads, but the game trims the list
    # back at its next market update and the extra job is gone. So the contract takes over one of the depot's own
    # slots: the live job with the most time left, keeping the expiry (and trailer spot) the game gave that slot.
    slots = [n for n in save.companies.get(src, []) if n in save.offers]
    if not slots:
        raise SaveError(f"{city_names.get(src[1], src[1])} has no job slots at that company in this save. "
                        "Pick another From company.")

    def left(n):
        e = save.offers[n].get("expiration_time", "")
        return int(e) - save.game_time if e.isdigit() else -1
    # a copy of this same job left by an earlier send (0.01 added an extra slot) is not a real slot: skip it
    same = f'"{dst[0]}.{dst[1]}"'
    slots = [n for n in slots if not (save.offers[n].get("target") == same and save.offers[n].get("cargo") == tpl["cargo"])] or slots
    slot = max(slots, key=left)
    if left(slot) >= 60:
        expires, place = int(save.offers[slot]["expiration_time"]), save.offers[slot].get("trailer_place", "0")
    else:                                   # only empty / expired slots: as long as the game's own longest job
        life = max((int(o["expiration_time"]) - save.game_time for o in real.values()
                    if o.get("expiration_time", "").isdigit() and int(o["expiration_time"]) > save.game_time), default=1440)
        expires, place = save.game_time + life, tpl.get("trailer_place", "0")
    offer = dict(tpl, target=f'"{dst[0]}.{dst[1]}"', expiration_time=str(expires), trailer_place=place,
                 urgency=urgency, shortest_distance_km=str(km), ferry_time="0", ferry_price="0")
    replaced = save.offers[slot]
    return {"source": src, "target": dst, "offer": offer, "km": km, "template": tpl_name, "slot": slot,
            "replaces": "" if replaced.get("cargo", "null") == "null" else replaced["cargo"].split(".", 1)[-1],
            "hours_to_take": max(1, (expires - save.game_time) // 60),
            "source_ships_cargo": bool(ships), "cargo": tpl["cargo"].split(".", 1)[-1]}


def _offer_block(name, offer, nl):
    keys = ["target", "expiration_time", "urgency", "shortest_distance_km", "ferry_time", "ferry_price", "cargo",
            "company_truck", "trailer_variant", "trailer_definition", "units_count", "fill_ratio", "trailer_place"]
    keys += [k for k in offer if k not in keys]
    return nl.join([f"job_offer_data : {name} {{"] + [f" {k}: {offer[k]}" for k in keys] + ["}", "", ""])


def add_offer(save, plan, contract_id=None):
    """Text of the save with the planned job written into its slot at the pickup company (the original text is
    not changed). The slot keeps its name, so the company's job list stays exactly as the game wrote it."""
    t, nl, name = save.text, save.nl, plan["slot"]
    at = t.find(f"job_offer_data : {name} {{")
    if at < 0:
        raise SaveError("Couldn't find the pickup company's job slot in the save.")
    end = t.find(nl + "}", at)
    if end < 0:
        raise SaveError("The job slot has an unexpected layout.")
    block = _offer_block(name, plan["offer"], nl)
    block = block[:block.rindex("}") + 1]                    # without the blank lines that follow a new unit
    return t[:at] + block + t[end + len(nl) + 1:], name


# ------------------------------------------------------------------------------------------- writing the slot
def write_slot(profile_dir, source_save_dir, contract_id, new_game_text, title):
    """Create (or replace) this app's own slot; returns its folder. Never touches any other save.

    The game's Load menu only lists numbered slots (save/1, save/2 ...), so the contract gets the next free number;
    sending the same contract again reuses the slot that still carries its name."""
    sdir = os.path.join(profile_dir, "save")
    if _slot_name(source_save_dir).startswith(TITLE_PREFIX):
        raise SaveError("Choose one of your own saves as the starting point, not a Fleet Manager Pro slot.")
    for old in os.listdir(sdir):                              # 0.01's hidden slots: ours, never shown by the game
        if old.startswith(SLOT_PREFIX) and os.path.isdir(os.path.join(sdir, old)):
            shutil.rmtree(os.path.join(sdir, old), ignore_errors=True)
    numbered = [int(d) for d in os.listdir(sdir) if d.isdigit() and os.path.isdir(os.path.join(sdir, d))]
    mine = [n for n in numbered if _slot_name(os.path.join(sdir, str(n))) == title]
    slot = os.path.join(sdir, str(mine[0] if mine else max(numbered, default=0) + 1))
    tmp = slot + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for f in os.listdir(source_save_dir):                    # preview image etc.
        src = os.path.join(source_save_dir, f)
        if os.path.isfile(src) and f.lower() not in ("game.sii", "info.sii") and not f.lower().endswith((".bak.sii", ".backup")):
            shutil.copy2(src, os.path.join(tmp, f))
    info = read_sii(os.path.join(source_save_dir, "info.sii"))
    safe = title.replace("\\", "").replace('"', "'")[:60]
    info = re.sub(r'\bname: "(?:[^"\\]|\\.)*"', f'name: "{safe}"', info, count=1)
    info = re.sub(r"\bfile_time: \d+", f"file_time: {int(time.time())}", info, count=1)
    with open(os.path.join(tmp, "info.sii"), "w", encoding="utf-8", newline="") as f:
        f.write(info)
    with open(os.path.join(tmp, "game.sii"), "w", encoding="utf-8", newline="") as f:
        f.write(new_game_text)
    if os.path.isdir(slot):
        shutil.rmtree(slot)
    os.replace(tmp, slot)
    return slot


def send_contract(game, profile_dir, source_save_dir, contract, lists, cities_xy, title):
    """Everything in one go. Returns a summary dict for the dispatcher."""
    save = Save(read_sii(os.path.join(source_save_dir, "game.sii")))
    plan = plan_offer(save, game, contract, lists, cities_xy)
    text, name = add_offer(save, plan, int(contract["id"]))
    Save(text)                                                   # re-read: the result must still parse
    slot = write_slot(profile_dir, source_save_dir, contract["id"], text, title)
    return dict(plan, slot=slot, offer_name=name, game_time=save.game_time)


def load_static(res_dir):
    """Cargo/company/city lists and city map positions bundled with the app."""
    with open(os.path.join(res_dir, "fmpro", "cargo_list.json"), encoding="utf-8") as f:
        lists = json.load(f)
    xy = {}
    for g in ("ats", "ets2"):
        try:
            with open(os.path.join(res_dir, "fmpro", "static", "map", f"{g}.json"), encoding="utf-8") as f:
                xy[g] = {c["n"].lower(): c["p"] for c in json.load(f)["cities"]}
        except (OSError, ValueError, KeyError):
            xy[g] = {}
    return lists, xy
