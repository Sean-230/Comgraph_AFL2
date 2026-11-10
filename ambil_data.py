"""Ambil data seluruh Surabaya dari OpenStreetMap (Overpass). Jalankan sekali: python ambil_data.py

Hasil: data/pos_damkar_surabaya.csv (nama, lon, lat, sumber) dan data/kecamatan_surabaya_semua.geojson (31 kecamatan).
Baris pos dengan sumber selain "OSM" (ditambah manual) tidak ditimpa.
"""
import csv, json
from pathlib import Path

import requests
from shapely.geometry import mapping, shape
from shapely.ops import polygonize, unary_union

DATA = Path("data")
POS_CSV = DATA / "pos_damkar_surabaya.csv"
AREA = 'area["name"="Surabaya"]["admin_level"="5"]->.a;'
# Pos yang tidak ada di OSM: koordinat dari Google Maps (sama dengan notebook)
MANUAL = [{"nama": "Pos Menur", "lon": 112.7629409, "lat": -7.2782297, "sumber": "Google Maps"}]
# Nama OSM yang kurang jelas -> nama yang dipakai di notebook / nama lokasi
NAMA = {449150188: "Pos Bulak", 449809850: "Pos Sukolilo (Keputih)", 451291149: "Pos Jambangan",
        451504869: "Rayon/UPTD III (SIER)", 451981973: "UPTD II Tambak Rejo", 452387333: "Pos Lakarsantri",
        452582470: "Rayon IV Wiyung", 457996476: "Pos Mastrip (Karang Pilang)", 459270806: "Pos Kandangan",
        460507505: "UPTD V Pakal", 467117596: "Pos Kali Rungkut",
        487570882: "Dinas Pemadam Kebakaran Kota Surabaya (Pasar Turi)", 1576065391: "Pos Pakuwon Indah"}


def overpass(q):
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": f"[out:json][timeout:120];{AREA}{q}"},
                      headers={"User-Agent": "gis-coursework"}, timeout=180)
    r.raise_for_status()
    return r.json()["elements"]


def ambil_pos():
    lama = list(csv.DictReader(POS_CSV.open())) if POS_CSV.exists() else MANUAL
    baris = [b for b in lama if b["sumber"] != "OSM"]
    for e in overpass('nwr["amenity"="fire_station"](area.a);out center tags;'):
        c = e if "lon" in e else e["center"]
        baris.append({"nama": NAMA.get(e["id"], e["tags"].get("name", f"Pos OSM {e['id']}")),
                      "lon": c["lon"], "lat": c["lat"], "sumber": "OSM"})
    with POS_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, ["nama", "lon", "lat", "sumber"])
        w.writeheader()
        w.writerows(sorted(baris, key=lambda b: b["nama"]))
    print(len(baris), "pos ->", POS_CSV)


def ambil_kecamatan():
    fitur = []
    for e in overpass('rel["boundary"="administrative"]["admin_level"="6"](area.a);out geom;'):
        garis = [{"type": "LineString", "coordinates": [(p["lon"], p["lat"]) for p in m["geometry"]]}
                 for m in e["members"] if m["type"] == "way" and m["role"] != "inner"]
        poligon = unary_union(list(polygonize(unary_union([shape(g) for g in garis]))))
        assert not poligon.is_empty, f"batas {e['tags'].get('name')} tidak tertutup"
        # disederhanakan ~20 m supaya ringan digambar tiap frame
        fitur.append({"type": "Feature", "properties": {"Kecamatan": e["tags"]["name"]},
                      "geometry": mapping(poligon.simplify(0.0002))})
    out = DATA / "kecamatan_surabaya_semua.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": fitur}))
    print(len(fitur), "kecamatan ->", out)


if __name__ == "__main__":
    ambil_pos()
    ambil_kecamatan()
