"""Developer tool: build the Fleet Map background data (fmpro/static/map/ats.json, ets2.json).

    python tools/build_map.py <folder with states.json and countries.json>

Input: public-domain boundary files
  * states.json     US Census state outlines   (us-atlas 3.0.1, states-10m.json)
  * countries.json  Natural Earth countries     (world-atlas 2.0.2, countries-50m.json)
Output: borders, region names and city positions converted to GAME world coordinates with the same map
projection the games use (Lambert conformal conic, 1:20 for ATS, 1:19 for ETS2). No game files are used.
"""
import json
import math
import os
import sys

R = 6370997.0
PROJ = {  # standard parallels, origin latitude, central meridian, scale
    "ats": dict(lat1=33.0, lat2=45.0, lat0=39.0, lon0=-96.0, k=0.05),
    "ets2": dict(lat1=37.0, lat2=65.0, lat0=50.0, lon0=15.0, k=1 / 19.0),
}


def projector(game):
    p = PROJ[game]
    f1, f2, f0 = (math.radians(p[k]) for k in ("lat1", "lat2", "lat0"))
    t = lambda f: math.tan(math.pi / 4 + f / 2)
    n = math.log(math.cos(f1) / math.cos(f2)) / math.log(t(f2) / t(f1))
    F = math.cos(f1) * t(f1) ** n / n
    rho0 = R * F / t(f0) ** n

    def to_game(lon, lat):
        rho = R * F / t(math.radians(lat)) ** n
        th = n * math.radians(lon - p["lon0"])
        x, y = rho * math.sin(th), rho0 - rho * math.cos(th)
        return round(x * p["k"], 1), round(-y * p["k"], 1)       # game z grows southwards
    return to_game


def decode_arcs(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def arc_ids(geom):
    """All arc indexes used by a Polygon / MultiPolygon geometry."""
    polys = [geom["arcs"]] if geom["type"] == "Polygon" else geom["arcs"]
    return [a if a >= 0 else ~a for poly in polys for ring in poly for a in ring]


def thin(points, tol):
    out = [points[0]]
    for p in points[1:-1]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= tol:
            out.append(p)
    if len(points) > 1:
        out.append(points[-1])
    return out


def lines_for(arcs, ids, box, to_game, tol):
    """Project arcs, cut them where they leave the lon/lat box, thin them."""
    lon0, lon1, lat0, lat1 = box
    lines = []
    for i in sorted(set(ids)):
        cur = []
        for lon, lat in arcs[i]:
            if lon0 <= lon <= lon1 and lat0 <= lat <= lat1:
                cur.append(to_game(lon, lat))
            elif cur:
                lines.append(cur)
                cur = []
        if cur:
            lines.append(cur)
    return [[c for p in thin(ln, tol) for c in p] for ln in lines if len(ln) > 1]


def centroid(arcs, geom, box, to_game):
    lon0, lon1, lat0, lat1 = box
    pts = [p for i in set(arc_ids(geom)) for p in arcs[i] if lon0 <= p[0] <= lon1 and lat0 <= p[1] <= lat1]
    if len(pts) < 8:
        return None
    lon = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    lat = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    return to_game(lon, lat)


# name, latitude, longitude, size (1 = always labelled, 3 = only when zoomed in)
ATS_CITIES = [
    ("Los Angeles", 34.05, -118.24, 1), ("San Diego", 32.72, -117.16, 1), ("San Francisco", 37.77, -122.42, 1),
    ("Sacramento", 38.58, -121.49, 2), ("Fresno", 36.74, -119.79, 2), ("Bakersfield", 35.37, -119.02, 2),
    ("Oakland", 37.80, -122.27, 3), ("Stockton", 37.96, -121.29, 3), ("Redding", 40.59, -122.39, 2),
    ("Eureka", 40.80, -124.16, 2), ("Barstow", 34.90, -117.02, 3), ("San Jose", 37.34, -121.89, 3),
    ("Santa Maria", 34.95, -120.44, 3), ("El Centro", 32.79, -115.56, 3), ("Ukiah", 39.15, -123.21, 3),
    ("Las Vegas", 36.17, -115.14, 1), ("Reno", 39.53, -119.81, 1), ("Elko", 40.83, -115.76, 2),
    ("Ely", 39.25, -114.89, 3), ("Winnemucca", 40.97, -117.74, 3), ("Tonopah", 38.07, -117.23, 3),
    ("Carson City", 39.16, -119.77, 3), ("Phoenix", 33.45, -112.07, 1), ("Tucson", 32.22, -110.97, 1),
    ("Flagstaff", 35.20, -111.65, 2), ("Yuma", 32.69, -114.63, 2), ("Kingman", 35.19, -114.05, 3),
    ("Show Low", 34.25, -110.03, 3), ("Page", 36.91, -111.46, 3), ("Nogales", 31.34, -110.94, 3),
    ("Albuquerque", 35.08, -106.65, 1), ("Santa Fe", 35.69, -105.94, 2), ("Las Cruces", 32.32, -106.76, 2),
    ("Roswell", 33.39, -104.52, 2), ("Farmington", 36.73, -108.22, 3), ("Gallup", 35.53, -108.74, 3),
    ("Carlsbad", 32.42, -104.23, 3), ("Tucumcari", 35.17, -103.72, 3), ("Clovis", 34.40, -103.21, 3),
    ("Portland", 45.52, -122.68, 1), ("Eugene", 44.05, -123.09, 2), ("Salem", 44.94, -123.04, 3),
    ("Medford", 42.33, -122.87, 2), ("Bend", 44.06, -121.31, 2), ("Pendleton", 45.67, -118.79, 3),
    ("Klamath Falls", 42.22, -121.78, 3), ("Ontario", 44.03, -116.96, 3), ("Astoria", 46.19, -123.83, 3),
    ("Seattle", 47.61, -122.33, 1), ("Spokane", 47.66, -117.43, 1), ("Tacoma", 47.25, -122.44, 3),
    ("Olympia", 47.04, -122.90, 3), ("Yakima", 46.60, -120.51, 2), ("Bellingham", 48.75, -122.48, 2),
    ("Kennewick", 46.21, -119.14, 3), ("Wenatchee", 47.42, -120.31, 3), ("Vancouver", 45.64, -122.66, 3),
    ("Salt Lake City", 40.76, -111.89, 1), ("St. George", 37.10, -113.58, 2), ("Moab", 38.57, -109.55, 3),
    ("Ogden", 41.22, -111.97, 3), ("Provo", 40.23, -111.66, 3), ("Cedar City", 37.68, -113.06, 3),
    ("Price", 39.60, -110.81, 3), ("Vernal", 40.46, -109.53, 3), ("Boise", 43.62, -116.20, 1),
    ("Idaho Falls", 43.49, -112.03, 2), ("Pocatello", 42.87, -112.45, 3), ("Twin Falls", 42.56, -114.46, 2),
    ("Coeur d'Alene", 47.68, -116.78, 2), ("Lewiston", 46.42, -117.02, 3), ("Denver", 39.74, -104.99, 1),
    ("Colorado Springs", 38.83, -104.82, 2), ("Pueblo", 38.25, -104.61, 2), ("Grand Junction", 39.06, -108.55, 2),
    ("Fort Collins", 40.59, -105.08, 3), ("Durango", 37.28, -107.88, 3), ("Alamosa", 37.47, -105.87, 3),
    ("Sterling", 40.63, -103.21, 3), ("Lamar", 38.09, -102.62, 3), ("Steamboat Springs", 40.48, -106.83, 3),
    ("Cheyenne", 41.14, -104.82, 1), ("Casper", 42.85, -106.31, 2), ("Laramie", 41.31, -105.59, 3),
    ("Rock Springs", 41.59, -109.20, 2), ("Gillette", 44.29, -105.50, 2), ("Sheridan", 44.80, -106.96, 3),
    ("Jackson", 43.48, -110.76, 3), ("Evanston", 41.27, -110.96, 3), ("Billings", 45.78, -108.50, 1),
    ("Missoula", 46.87, -113.99, 2), ("Great Falls", 47.50, -111.30, 2), ("Helena", 46.59, -112.04, 2),
    ("Bozeman", 45.68, -111.04, 3), ("Butte", 46.00, -112.53, 3), ("Kalispell", 48.20, -114.31, 3),
    ("Miles City", 46.41, -105.84, 3), ("Glendive", 47.11, -104.71, 3), ("Havre", 48.55, -109.68, 3),
    ("Dallas", 32.78, -96.80, 1), ("Houston", 29.76, -95.37, 1), ("San Antonio", 29.42, -98.49, 1),
    ("Austin", 30.27, -97.74, 2), ("El Paso", 31.76, -106.49, 1), ("Fort Worth", 32.76, -97.33, 3),
    ("Amarillo", 35.22, -101.83, 2), ("Lubbock", 33.58, -101.86, 2), ("Corpus Christi", 27.80, -97.40, 2),
    ("Laredo", 27.51, -99.51, 2), ("Odessa", 31.85, -102.37, 2), ("Abilene", 32.45, -99.73, 3),
    ("Waco", 31.55, -97.15, 3), ("Beaumont", 30.08, -94.13, 3), ("Brownsville", 25.90, -97.50, 2),
    ("McAllen", 26.20, -98.23, 3), ("San Angelo", 31.46, -100.44, 3), ("Wichita Falls", 33.91, -98.49, 3),
    ("Tyler", 32.35, -95.30, 3), ("Texarkana", 33.43, -94.05, 3), ("Del Rio", 29.36, -100.90, 3),
    ("Galveston", 29.30, -94.80, 3), ("Longview", 32.50, -94.74, 3), ("Victoria", 28.81, -97.00, 3),
    ("Wichita", 37.69, -97.34, 1), ("Topeka", 39.05, -95.68, 2), ("Kansas City", 39.10, -94.58, 1),
    ("Dodge City", 37.75, -100.02, 3), ("Salina", 38.84, -97.61, 3), ("Garden City", 37.97, -100.87, 3),
    ("Hays", 38.88, -99.33, 3), ("Colby", 39.40, -101.05, 3), ("Emporia", 38.40, -96.18, 3),
    ("Oklahoma City", 35.47, -97.52, 1), ("Tulsa", 36.15, -95.99, 1), ("Lawton", 34.61, -98.39, 3),
    ("Enid", 36.40, -97.88, 3), ("Guymon", 36.68, -101.48, 3), ("Ardmore", 34.17, -97.14, 3),
    ("McAlester", 34.93, -95.77, 3), ("Woodward", 36.43, -99.39, 3), ("Omaha", 41.26, -95.93, 1),
    ("Lincoln", 40.81, -96.70, 2), ("Grand Island", 40.93, -98.34, 3), ("North Platte", 41.12, -100.77, 2),
    ("Scottsbluff", 41.87, -103.67, 3), ("Norfolk", 42.03, -97.42, 3), ("Valentine", 42.87, -100.55, 3),
    ("Little Rock", 34.75, -92.29, 1), ("Fort Smith", 35.39, -94.40, 2), ("Fayetteville", 36.06, -94.16, 3),
    ("Jonesboro", 35.84, -90.70, 3), ("Pine Bluff", 34.23, -92.00, 3), ("El Dorado", 33.21, -92.67, 3),
    ("St. Louis", 38.63, -90.20, 1), ("Springfield", 37.21, -93.29, 2), ("Columbia", 38.95, -92.33, 3),
    ("Joplin", 37.08, -94.51, 3), ("Jefferson City", 38.58, -92.17, 3), ("Cape Girardeau", 37.31, -89.52, 3),
    ("St. Joseph", 39.77, -94.85, 3), ("New Orleans", 29.95, -90.07, 1), ("Baton Rouge", 30.45, -91.19, 2),
    ("Shreveport", 32.52, -93.75, 2), ("Lafayette", 30.22, -92.02, 3), ("Lake Charles", 30.23, -93.22, 3),
    ("Monroe", 32.51, -92.12, 3), ("Alexandria", 31.31, -92.45, 3), ("Des Moines", 41.59, -93.62, 1),
    ("Cedar Rapids", 41.98, -91.67, 3), ("Sioux City", 42.50, -96.40, 3), ("Davenport", 41.52, -90.58, 3),
    ("Sioux Falls", 43.55, -96.73, 2), ("Rapid City", 44.08, -103.23, 2), ("Pierre", 44.37, -100.35, 3),
    ("Fargo", 46.88, -96.79, 2), ("Bismarck", 46.81, -100.78, 2), ("Minot", 48.23, -101.30, 3),
    ("Minneapolis", 44.98, -93.27, 1), ("Duluth", 46.79, -92.10, 2), ("Chicago", 41.88, -87.63, 1),
    ("Milwaukee", 43.04, -87.91, 2), ("Memphis", 35.15, -90.05, 1), ("Nashville", 36.16, -86.78, 1),
    ("Jackson MS", 32.30, -90.18, 2), ("Birmingham", 33.52, -86.80, 2), ("Atlanta", 33.75, -84.39, 1),
    ("Indianapolis", 39.77, -86.16, 2), ("Louisville", 38.25, -85.76, 2), ("Detroit", 42.33, -83.05, 1),
    ("Huntsville", 34.73, -86.59, 3), ("Mobile", 30.69, -88.04, 3),
]
ETS2_CITIES = [
    ("London", 51.51, -0.13, 1), ("Birmingham", 52.48, -1.90, 2), ("Manchester", 53.48, -2.24, 2),
    ("Liverpool", 53.41, -2.98, 3), ("Glasgow", 55.86, -4.25, 2), ("Edinburgh", 55.95, -3.19, 2),
    ("Aberdeen", 57.15, -2.09, 3), ("Cardiff", 51.48, -3.18, 3), ("Newcastle", 54.98, -1.61, 3),
    ("Dover", 51.13, 1.31, 3), ("Plymouth", 50.38, -4.14, 3), ("Southampton", 50.90, -1.40, 3),
    ("Paris", 48.86, 2.35, 1), ("Lyon", 45.76, 4.84, 2), ("Marseille", 43.30, 5.37, 2),
    ("Bordeaux", 44.84, -0.58, 2), ("Toulouse", 43.60, 1.44, 3), ("Nantes", 47.22, -1.55, 3),
    ("Lille", 50.63, 3.06, 3), ("Strasbourg", 48.57, 7.75, 3), ("Calais", 50.95, 1.86, 3),
    ("Nice", 43.70, 7.27, 3), ("Brest", 48.39, -4.49, 3), ("Le Havre", 49.49, 0.11, 3),
    ("Brussels", 50.85, 4.35, 2), ("Amsterdam", 52.37, 4.90, 1), ("Rotterdam", 51.92, 4.48, 3),
    ("Luxembourg", 49.61, 6.13, 3), ("Berlin", 52.52, 13.40, 1), ("Hamburg", 53.55, 9.99, 2),
    ("Munich", 48.14, 11.58, 2), ("Frankfurt", 50.11, 8.68, 2), ("Cologne", 50.94, 6.96, 3),
    ("Stuttgart", 48.78, 9.18, 3), ("Dresden", 51.05, 13.74, 3), ("Hannover", 52.37, 9.74, 3),
    ("Nuremberg", 49.45, 11.08, 3), ("Bremen", 53.08, 8.80, 3), ("Leipzig", 51.34, 12.37, 3),
    ("Dortmund", 51.51, 7.47, 3), ("Rostock", 54.09, 12.14, 3), ("Kiel", 54.32, 10.14, 3),
    ("Zurich", 47.38, 8.54, 2), ("Geneva", 46.20, 6.14, 3), ("Bern", 46.95, 7.45, 3),
    ("Vienna", 48.21, 16.37, 1), ("Salzburg", 47.81, 13.04, 3), ("Innsbruck", 47.27, 11.39, 3),
    ("Graz", 47.07, 15.44, 3), ("Prague", 50.08, 14.44, 1), ("Brno", 49.20, 16.61, 3),
    ("Bratislava", 48.15, 17.11, 2), ("Kosice", 48.72, 21.26, 3), ("Budapest", 47.50, 19.04, 1),
    ("Warsaw", 52.23, 21.01, 1), ("Krakow", 50.06, 19.94, 2), ("Gdansk", 54.35, 18.65, 2),
    ("Wroclaw", 51.11, 17.03, 3), ("Poznan", 52.41, 16.93, 3), ("Szczecin", 53.43, 14.55, 3),
    ("Lodz", 51.76, 19.46, 3), ("Copenhagen", 55.68, 12.57, 1), ("Aarhus", 56.16, 10.20, 3),
    ("Oslo", 59.91, 10.75, 1), ("Bergen", 60.39, 5.32, 2), ("Stavanger", 58.97, 5.73, 3),
    ("Kristiansand", 58.15, 8.00, 3), ("Stockholm", 59.33, 18.07, 1), ("Gothenburg", 57.71, 11.97, 2),
    ("Malmo", 55.60, 13.00, 3), ("Helsinki", 60.17, 24.94, 1), ("Turku", 60.45, 22.27, 3),
    ("Tampere", 61.50, 23.76, 3), ("Tallinn", 59.44, 24.75, 2), ("Riga", 56.95, 24.11, 2),
    ("Vilnius", 54.69, 25.28, 2), ("Kaunas", 54.90, 23.90, 3), ("St. Petersburg", 59.93, 30.34, 2),
    ("Milan", 45.46, 9.19, 1), ("Rome", 41.90, 12.50, 1), ("Naples", 40.85, 14.27, 2),
    ("Turin", 45.07, 7.69, 3), ("Venice", 45.44, 12.32, 3), ("Bologna", 44.49, 11.34, 3),
    ("Florence", 43.77, 11.26, 3), ("Genoa", 44.41, 8.93, 3), ("Bari", 41.12, 16.87, 3),
    ("Palermo", 38.12, 13.36, 3), ("Madrid", 40.42, -3.70, 1), ("Barcelona", 41.39, 2.17, 1),
    ("Valencia", 39.47, -0.38, 2), ("Seville", 37.39, -5.98, 2), ("Bilbao", 43.26, -2.93, 3),
    ("Zaragoza", 41.65, -0.89, 3), ("Malaga", 36.72, -4.42, 3), ("A Coruna", 43.36, -8.41, 3),
    ("Lisbon", 38.72, -9.14, 1), ("Porto", 41.15, -8.61, 2), ("Zagreb", 45.81, 15.98, 2),
    ("Ljubljana", 46.06, 14.51, 3), ("Belgrade", 44.79, 20.45, 2), ("Sarajevo", 43.86, 18.41, 3),
    ("Split", 43.51, 16.44, 3), ("Bucharest", 44.43, 26.10, 1), ("Cluj-Napoca", 46.77, 23.60, 3),
    ("Constanta", 44.18, 28.63, 3), ("Timisoara", 45.75, 21.23, 3), ("Sofia", 42.70, 23.32, 1),
    ("Varna", 43.21, 27.91, 3), ("Istanbul", 41.01, 28.98, 1), ("Athens", 37.98, 23.73, 1),
    ("Thessaloniki", 40.64, 22.94, 2), ("Skopje", 42.00, 21.43, 3), ("Tirana", 41.33, 19.82, 3),
    ("Podgorica", 42.44, 19.26, 3), ("Pristina", 42.66, 21.17, 3),
]


def build(folder, out_dir):
    states = json.load(open(os.path.join(folder, "states.json"), encoding="utf-8"))
    world = json.load(open(os.path.join(folder, "countries.json"), encoding="utf-8"))
    s_arcs, w_arcs = decode_arcs(states), decode_arcs(world)
    os.makedirs(out_dir, exist_ok=True)

    # ---- ATS: lower-48 state lines + Canada / Mexico outlines
    tg = projector("ats")
    box = (-130.0, -62.0, 14.0, 56.0)
    skip = {"02", "15", "60", "66", "69", "72", "78"}            # Alaska, Hawaii, territories
    geoms = [g for g in states["objects"]["states"]["geometries"] if g["id"] not in skip]
    lines = lines_for(s_arcs, [a for g in geoms for a in arc_ids(g)], box, tg, 120)
    lines += lines_for(w_arcs, [a for g in world["objects"]["countries"]["geometries"]
                                if g.get("id") in ("124", "484") for a in arc_ids(g)], box, tg, 160)
    names = [{"n": g["properties"]["name"], "p": centroid(s_arcs, g, box, tg)} for g in geoms]
    ats = {"game": "ats", "note": "Real-world geography in the game's map projection (1:20).",
           "lines": lines, "regions": [r for r in names if r["p"]],
           "cities": [{"n": n, "p": tg(lon, lat), "s": s} for n, lat, lon, s in ATS_CITIES]}
    json.dump(ats, open(os.path.join(out_dir, "ats.json"), "w", encoding="utf-8"), separators=(",", ":"))

    # ---- ETS2: European country outlines
    tg = projector("ets2")
    box = (-12.0, 46.0, 34.0, 71.5)
    geoms = [g for g in world["objects"]["countries"]["geometries"]
             if any(box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3] for i in set(arc_ids(g)) for p in w_arcs[i][:1])]
    lines = lines_for(w_arcs, [a for g in geoms for a in arc_ids(g)], box, tg, 140)
    names = [{"n": g["properties"]["name"], "p": centroid(w_arcs, g, box, tg)} for g in geoms]
    ets = {"game": "ets2", "note": "Real-world geography in the game's map projection (1:19). ETS2 moves and enlarges "
                                   "the UK, so positions there are approximate until learned from driving.",
           "lines": lines, "regions": [r for r in names if r["p"]],
           "cities": [{"n": n, "p": tg(lon, lat), "s": s} for n, lat, lon, s in ETS2_CITIES]}
    json.dump(ets, open(os.path.join(out_dir, "ets2.json"), "w", encoding="utf-8"), separators=(",", ":"))
    for g, d in (("ats", ats), ("ets2", ets)):
        size = os.path.getsize(os.path.join(out_dir, g + ".json"))
        print(f"{g}: {len(d['lines'])} border lines, {sum(len(x) // 2 for x in d['lines'])} points, "
              f"{len(d['regions'])} regions, {len(d['cities'])} cities, {size // 1024} KB")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    build(sys.argv[1], os.path.join(os.path.dirname(here), "fmpro", "static", "map"))
