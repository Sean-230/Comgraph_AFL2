"""Simulasi respon Damkar Surabaya (pygame). Jalankan: python app.py

Pilih lokasi kebakaran (klik peta, atau ketik nama tempat / "lat, lon" lalu Enter). Aplikasi mencari pos Damkar
yang paling dekat (garis lurus) dan paling cepat tiba (rute TomTom dengan lalu lintas), seperti bagian 6 notebook.

Kontrol: geser = drag, zoom = scroll atau tombol +/-, panah atas/bawah = ganti jam berangkat (atau klik kotak jam dan ketik HH:MM 24 jam, lalu Enter), Esc = hapus pencarian.
"""
import csv, json, math, os, re, threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pygame
import requests
from shapely.geometry import Point, shape

DATA = Path("data")
TILE_DIR = Path("output/tiles")            # cache tile OSM di disk
WIB = ZoneInfo("Asia/Jakarta")
BATAS_TEMPUH_MENIT = 5                      # tahap perjalanan dari standar 15 menit (sama dengan notebook)
N_KANDIDAT = 5                              # rute hanya dihitung untuk N pos terdekat (hemat kuota TomTom)
PANEL_W = 400
UA = {"User-Agent": "gis-coursework-pygame"}
PUSAT = (112.72, -7.28)                     # lon, lat tengah Surabaya

KEY_FILE = Path(".tomtom_key")
KEY = os.environ.get("TOMTOM_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else None)


# ---------- geometri ----------
def merc(lon, lat):
    """lon/lat -> Web Mercator ternormalisasi (0..1), sama dengan skema tile OSM."""
    return (lon + 180) / 360, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2


def merc_balik(mx, my):
    return mx * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my))))


def jarak_km(lon1, lat1, lon2, lat2):
    """Jarak garis lurus (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


# ---------- data ----------
def muat_pos():
    with (DATA / "pos_damkar_surabaya.csv").open() as f:
        return [{"nama": b["nama"], "lon": float(b["lon"]), "lat": float(b["lat"])} for b in csv.DictReader(f)]


def muat_kecamatan():
    gj = json.loads((DATA / "kecamatan_surabaya_semua.geojson").read_text())
    kec = []
    for f in gj["features"]:
        geom = shape(f["geometry"])
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        kec.append({"nama": f["properties"]["Kecamatan"], "geom": geom,
                    "ring": [[merc(x, y) for x, y in p.exterior.coords] for p in polys]})
    return kec


# ---------- TomTom ----------
HARI = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")


def jam_teks(menit):
    return f"{menit // 60:02d}:{menit % 60:02d}"


def berangkat(menit):
    """Jam tsb (menit sejak 00:00, WIB) pada tanggal hari ini -> TomTom memakai pola lalu lintas historis hari dan jam itu."""
    return datetime.now(WIB).replace(hour=menit // 60, minute=menit % 60, second=0, microsecond=0).isoformat()


def rute(pos, lon, lat, jam):
    """Rute tercepat pos -> lokasi. jam=None: berangkat sekarang (lalu lintas langsung). -> (menit, km, [(lon, lat)])"""
    params = {"key": KEY, "travelMode": "car", "traffic": "true", "routeType": "fastest"}
    if jam is not None:
        params["departAt"] = berangkat(jam)
    r = requests.get(f"https://api.tomtom.com/routing/1/calculateRoute/{pos['lat']},{pos['lon']}:{lat},{lon}/json",
                     params=params, timeout=30)
    r.raise_for_status()
    rt = r.json()["routes"][0]
    titik = [(t["longitude"], t["latitude"]) for leg in rt["legs"] for t in leg["points"]]
    return rt["summary"]["travelTimeInSeconds"] / 60, rt["summary"]["lengthInMeters"] / 1000, titik


def cari_tempat(teks):
    """Nama tempat -> [(lon, lat, label)], maksimal 5 hasil. Bias ke Surabaya."""
    r = requests.get(f"https://api.tomtom.com/search/2/search/{requests.utils.quote(teks, safe='')}.json", timeout=30,
                     params={"key": KEY, "countrySet": "ID", "lat": PUSAT[1], "lon": PUSAT[0], "radius": 30000, "limit": 5})
    r.raise_for_status()
    return [(h["position"]["lon"], h["position"]["lat"],
             ", ".join(filter(None, [h.get("poi", {}).get("name"), h["address"].get("freeformAddress")])) or teks)
            for h in r.json()["results"]]


# ---------- gaya & alat gambar ----------
C = {"teks": (32, 33, 36), "redup": (95, 99, 104), "garis": (218, 220, 224), "latar": (241, 243, 244),
     "kartu": (255, 255, 255), "merah": (217, 48, 37), "hijau": (30, 142, 62), "biru": (26, 115, 232),
     "jingga": (251, 188, 4), "ungu": (103, 80, 164)}
F = {}   # font, diisi setelah pygame.init()
lingkar = getattr(pygame.draw, "aacircle", pygame.draw.circle)   # lingkaran halus (pygame-ce)


def muat_font():
    for nama, (ukuran, tebal) in {"kecil": (12, False), "biasa": (14, False), "tebal": (14, True), "sedang": (16, True),
                                  "judul": (20, True), "angka": (40, True)}.items():
        F[nama] = pygame.font.SysFont("helveticaneue,segoeui,dejavusans,arial", ukuran, bold=tebal)


def tulis(layar, s, f, warna, x, y, maks=None, rata="kiri"):
    """Gambar teks; dipotong dengan "..." kalau lebih lebar dari maks. -> lebar teks"""
    font = F[f]
    if maks and font.size(s)[0] > maks:
        while s and font.size(s + "...")[0] > maks:
            s = s[:-1]
        s += "..."
    img = font.render(s, True, warna)
    x -= img.get_width() if rata == "kanan" else img.get_width() // 2 if rata == "tengah" else 0
    layar.blit(img, (x, y))
    return img.get_width()


def kartu(layar, rect, warna=None, radius=12):
    pygame.draw.rect(layar, warna or C["kartu"], rect, border_radius=radius)
    pygame.draw.rect(layar, C["garis"], rect, 1, border_radius=radius)


def pil(layar, s, x, y, f="kecil", warna=None, latar=None):
    """Label kecil berlatar bulat di atas peta."""
    w, h = F[f].size(s)
    r = pygame.Rect(x, y, w + 14, h + 6)
    pygame.draw.rect(layar, latar or C["kartu"], r, border_radius=r.h // 2)
    if not latar:
        pygame.draw.rect(layar, C["garis"], r, 1, border_radius=r.h // 2)
    tulis(layar, s, f, warna or C["teks"], x + 7, y + 3)


def putar(layar, pusat, t, r=10):
    """Indikator memuat (busur berputar)."""
    a = -t / 130
    pygame.draw.arc(layar, C["biru"], (pusat[0] - r, pusat[1] - r, 2 * r, 2 * r), a, a + 4.4, 3)


# ---------- aplikasi ----------
class App:
    def __init__(self):
        self.pos, self.kec = muat_pos(), muat_kecamatan()
        self.cx, self.cy = merc(*PUSAT)     # pusat tampilan (mercator ternormalisasi)
        self.zoom = 12
        self.tiles, self.tile_diminta = {}, set()
        self.teks, self.pesan = "", "Klik peta atau ketik lokasi kebakaran, lalu Enter"
        self.hasil = []                     # hasil pencarian yang bisa diklik: [(lon, lat, label)]
        self.lokasi = None                  # (lon, lat, label, kecamatan)
        self.kandidat = []                  # pos terdekat + hasil rute
        self.jam = None                     # None = sekarang; selain itu menit sejak 00:00 (0-1439)
        self.ketik_jam, self.fokus_jam, self.galat_jam = "", False, False   # kotak ketik jam HH:MM
        self.gen = 0                        # nomor pilihan; hasil thread lama diabaikan
        self.klik, self.kotak_cari, self._ov = [], None, None
        self.tekan, self.geser = None, 0
        self._kursor = None

    # --- tampilan <-> koordinat ---
    def skala(self):
        return 256 * 2 ** self.zoom

    def ke_layar(self, mx, my):
        s = self.skala()
        return (mx - self.cx) * s + self.map_w / 2, (my - self.cy) * s + self.h / 2

    def ke_lonlat(self, x, y):
        s = self.skala()
        return merc_balik(self.cx + (x - self.map_w / 2) / s, self.cy + (y - self.h / 2) / s)

    # --- tile OSM ---
    def tile(self, z, x, y):
        k = (z, x, y)
        if k not in self.tiles and k not in self.tile_diminta:
            self.tile_diminta.add(k)
            threading.Thread(target=self._unduh_tile, args=k, daemon=True).start()
        return self.tiles.get(k)

    def _unduh_tile(self, z, x, y):
        f = TILE_DIR / f"{z}/{x}/{y}.png"
        try:
            if not f.exists():
                r = requests.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png", headers=UA, timeout=20)
                r.raise_for_status()
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
            # shortcut: tile tidak pernah dibuang dari memori, tambahkan LRU kalau sesi sangat panjang
            self.tiles[(z, x, y)] = pygame.image.load(str(f))
        except Exception:
            self.tile_diminta.discard((z, x, y))   # coba lagi di frame berikutnya yang membutuhkannya

    # --- logika inti ---
    def pilih(self, lon, lat, label=None):
        """Simulasikan kebakaran di (lon, lat): urutkan pos menurut garis lurus, lalu hitung rute N terdekat."""
        self.gen += 1
        p = Point(lon, lat)
        kec = next((k["nama"] for k in self.kec if k["geom"].contains(p)), None)
        self.lokasi = (lon, lat, label or "Titik pilihan di peta", kec or "di luar Surabaya")
        urut = sorted(self.pos, key=lambda q: jarak_km(q["lon"], q["lat"], lon, lat))
        # shortcut: pos tercepat diasumsikan ada di antara N_KANDIDAT terdekat garis lurus; naikkan N kalau ragu
        self.kandidat = [{"pos": q, "lurus": jarak_km(q["lon"], q["lat"], lon, lat), "menit": None, "km": None,
                          "garis": None, "galat": None} for q in urut[:N_KANDIDAT]]
        if KEY:
            self.pesan = "Menghitung rute..."
            threading.Thread(target=self._hitung_rute, args=(self.gen, self.kandidat, lon, lat, self.jam), daemon=True).start()
        else:
            self.pesan = "Tanpa API key TomTom: hanya jarak garis lurus"

    def _hitung_rute(self, gen, kandidat, lon, lat, jam):
        for k in kandidat:
            if gen != self.gen:
                return
            try:
                k["menit"], k["km"], k["garis"] = rute(k["pos"], lon, lat, jam)
            except Exception as e:
                k["galat"] = str(e)[:60]
        if gen == self.gen:
            self.pesan = "Selesai"

    def terapkan_jam(self):
        m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})\s*", self.ketik_jam)
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            self.galat_jam = True
            return
        self.fokus_jam = False
        self.ganti_jam(int(m[1]) * 60 + int(m[2]))

    def cari(self):
        teks = self.teks.strip()
        if not teks:
            return
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*", teks)   # "lat, lon"
        if m:
            return self.lompat(float(m[2]), float(m[1]))
        if not KEY:
            self.pesan = "Pencarian nama butuh API key TomTom; ketik \"lat, lon\""
            return
        self.pesan = f"Mencari \"{teks}\"..."

        def kerja():
            try:
                h = cari_tempat(teks)
                if len(h) == 1:
                    self.lompat(*h[0])
                elif h:   # hasil teratas TomTom belum tentu tempat yang dimaksud -> pengguna memilih
                    self.hasil, self.pesan = h, "Pilih salah satu hasil pencarian"
                else:
                    self.pesan = f"\"{teks}\" tidak ditemukan"
            except Exception as e:
                self.pesan = f"Pencarian gagal: {str(e)[:50]}"
        threading.Thread(target=kerja, daemon=True).start()

    def lompat(self, lon, lat, label=None):
        self.hasil = []
        self.cx, self.cy = merc(lon, lat)
        self.zoom = max(self.zoom, 14)
        self.pilih(lon, lat, label)

    def ganti_jam(self, jam):
        if jam == self.jam:
            return
        self.jam = jam
        self.ketik_jam, self.galat_jam = ("" if jam is None else jam_teks(jam)), False
        if self.lokasi:
            self.pilih(*self.lokasi[:3])

    def tercepat(self):
        if any(k["menit"] is None and not k["galat"] for k in self.kandidat):
            return None   # tunggu semua rute selesai supaya "tercepat" tidak berganti-ganti
        ada = [k for k in self.kandidat if k["menit"] is not None]
        return min(ada, key=lambda k: k["menit"]) if ada else None

    # --- gambar ---
    def gambar(self, layar):
        if not F:
            muat_font()
        self.w, self.h = layar.get_size()
        self.map_w = self.w - PANEL_W
        self.klik = []                       # [(rect, fungsi)] dibangun ulang tiap frame; yang terakhir = paling atas
        t = pygame.time.get_ticks()
        mouse = pygame.mouse.get_pos()
        layar.fill((229, 227, 223))
        s, n = self.skala(), 2 ** self.zoom
        x0, y0 = self.cx * s - self.map_w / 2, self.cy * s - self.h / 2   # piksel dunia di pojok kiri atas
        for tx in range(int(x0 // 256), int((x0 + self.map_w) // 256) + 1):
            for ty in range(int(y0 // 256), int((y0 + self.h) // 256) + 1):
                if 0 <= tx < n and 0 <= ty < n and (img := self.tile(self.zoom, tx, ty)):
                    layar.blit(img, (tx * 256 - x0, ty * 256 - y0))

        # lapisan transparan: batas kecamatan, kecamatan terpilih, denyut titik api
        if self._ov is None or self._ov.get_size() != (self.map_w, self.h):
            self._ov = pygame.Surface((self.map_w, self.h), pygame.SRCALPHA)
        ov = self._ov
        ov.fill((0, 0, 0, 0))
        for k in self.kec:
            for ring in k["ring"]:
                pts = [self.ke_layar(*p) for p in ring]
                if self.lokasi and k["nama"] == self.lokasi[3]:
                    pygame.draw.polygon(ov, (*C["ungu"], 40), pts)
                pygame.draw.lines(ov, (*C["ungu"], 130), True, pts, 2)
        if self.lokasi:
            fx, fy = self.ke_layar(*merc(*self.lokasi[:2]))
            f = (t % 1600) / 1600
            pygame.draw.circle(ov, (*C["jingga"], int(140 * (1 - f))), (fx, fy), 12 + 26 * f)
        layar.blit(ov, (0, 0))

        cepat = self.tercepat()
        for k in sorted(self.kandidat, key=lambda k: k is cepat):   # rute tercepat digambar paling atas
            if k["garis"]:
                pts = [self.ke_layar(*merc(*p)) for p in k["garis"]]
                if k is cepat:
                    pygame.draw.lines(layar, C["kartu"], False, pts, 10)
                    pygame.draw.lines(layar, C["hijau"], False, pts, 6)
                else:
                    pygame.draw.lines(layar, (140, 146, 152), False, pts, 3)

        nama_kandidat = {k["pos"]["nama"] for k in self.kandidat}
        hover = None
        for q in self.pos:
            x, y = self.ke_layar(*merc(q["lon"], q["lat"]))
            utama, kand = bool(cepat) and q is cepat["pos"], q["nama"] in nama_kandidat
            r = 9 if utama else 7 if kand else 6
            lingkar(layar, C["kartu"], (x, y), r + 3)
            lingkar(layar, C["hijau"] if utama else C["merah"], (x, y), r)
            lingkar(layar, C["kartu"], (x, y), max(2, r - 5))
            if mouse[0] < self.map_w and math.hypot(mouse[0] - x, mouse[1] - y) < r + 6:
                hover = (q, x, y)
            if utama:
                pil(layar, f"{q['nama']} · {cepat['menit']:.1f} mnt", x + r + 7, y - 11, "tebal", C["kartu"], C["hijau"])
            elif kand or self.zoom >= 14:
                pil(layar, q["nama"], x + r + 7, y - 10)
        if hover and not (hover[0]["nama"] in nama_kandidat or self.zoom >= 14):
            pil(layar, hover[0]["nama"], hover[1] + 13, hover[2] - 10)

        if self.lokasi:   # titik kebakaran
            lingkar(layar, C["kartu"], (fx, fy), 13)
            lingkar(layar, C["merah"], (fx, fy), 10)
            lingkar(layar, C["jingga"], (fx, fy), 6)
            lingkar(layar, (255, 235, 59), (fx, fy), 3)

        self.gambar_cari(layar, t)
        # tombol zoom
        for i, (label, dz) in enumerate((("+", 1), ("-", -1))):
            r = pygame.Rect(self.map_w - 52, self.h - 106 + i * 40, 36, 36)
            kartu(layar, r, (232, 240, 254) if r.collidepoint(mouse) else C["kartu"], 8)
            tulis(layar, label, "judul", C["teks"], r.centerx, r.y + 5, rata="tengah")
            self.klik.append((r, lambda dz=dz: self.zoom_ke(dz, self.map_w / 2, self.h / 2)))
        pil(layar, "© OpenStreetMap contributors · rute: TomTom", 8, self.h - 26, warna=C["redup"])

        self.gambar_panel(layar, cepat, t)
        kena = next((r for r, _ in reversed(self.klik) if r.collidepoint(mouse)), None)
        kursor = (pygame.SYSTEM_CURSOR_IBEAM if kena is self.kotak_cari else
                  pygame.SYSTEM_CURSOR_HAND if kena else pygame.SYSTEM_CURSOR_ARROW)
        if kursor != self._kursor:
            self._kursor = kursor
            try:
                pygame.mouse.set_cursor(kursor)
            except pygame.error:
                pass   # kursor hanya hiasan; beberapa driver video tidak mendukungnya

    def gambar_cari(self, layar, t):
        mouse = pygame.mouse.get_pos()
        kotak = self.kotak_cari = pygame.Rect(16, 16, min(460, self.map_w - 32), 44)
        pygame.draw.rect(layar, (196, 199, 204), kotak.move(0, 2), border_radius=22)   # bayangan
        kartu(layar, kotak, radius=22)
        self.klik.append((kotak, lambda: None))   # klik di kotak tidak memilih titik peta
        lingkar(layar, C["redup"], (kotak.x + 24, kotak.centery - 2), 7, 2)   # ikon kaca pembesar
        pygame.draw.line(layar, C["redup"], (kotak.x + 29, kotak.centery + 3), (kotak.x + 34, kotak.centery + 8), 3)
        tx, ty, maks = kotak.x + 46, kotak.y + 13, kotak.w - 92
        if self.teks:
            w = tulis(layar, self.teks, "biasa", C["teks"], tx, ty, maks)
            if t % 1000 < 550:   # kursor berkedip
                pygame.draw.line(layar, C["teks"], (tx + w + 2, ty), (tx + w + 2, ty + 17), 1)
            r = pygame.Rect(kotak.right - 38, kotak.y + 8, 28, 28)
            if self.pesan.startswith("Mencari"):
                putar(layar, r.center, t)
            else:   # tombol hapus
                lingkar(layar, (232, 234, 237) if r.collidepoint(mouse) else C["kartu"], r.center, 13)
                for d in (-1, 1):
                    pygame.draw.line(layar, C["redup"], (r.centerx - 5, r.centery - 5 * d), (r.centerx + 5, r.centery + 5 * d), 2)
                self.klik.append((r, self.hapus_cari))
        else:
            tulis(layar, "Cari lokasi kebakaran, lalu tekan Enter", "biasa", (150, 154, 160), tx, ty, maks)

        y = kotak.bottom + 6
        if self.hasil:   # daftar hasil pencarian
            kartu(layar, (kotak.x, y, kotak.w, len(self.hasil) * 46 + 8), radius=12)
            for i, h in enumerate(self.hasil):
                r = pygame.Rect(kotak.x + 4, y + 4 + i * 46, kotak.w - 8, 46)
                if r.collidepoint(mouse):
                    pygame.draw.rect(layar, (232, 240, 254), r, border_radius=8)
                nama, _, alamat = h[2].partition(", ")
                lingkar(layar, C["merah"], (r.x + 18, r.centery), 5)
                tulis(layar, nama, "tebal", C["teks"], r.x + 36, r.y + 6, r.w - 46)
                tulis(layar, alamat or "Surabaya", "kecil", C["redup"], r.x + 36, r.y + 25, r.w - 46)
                self.klik.append((r, lambda h=h, nama=nama: (setattr(self, "teks", nama), self.lompat(*h))))
        elif self.pesan.startswith(("Pencarian", '"')):   # galat / tidak ditemukan
            pil(layar, self.pesan, kotak.x + 8, y, warna=C["kartu"], latar=C["merah"])

    def chip(self, layar, label, x, y, aktif, fn):
        r = pygame.Rect(x, y, F["biasa"].size(label)[0] + 22, 30)
        hov = r.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(layar, C["biru"] if aktif else (232, 240, 254) if hov else C["kartu"], r, border_radius=15)
        if not aktif:
            pygame.draw.rect(layar, C["garis"], r, 1, border_radius=15)
        tulis(layar, label, "biasa", C["kartu"] if aktif else C["teks"], r.centerx, y + 6, rata="tengah")
        self.klik.append((r, fn))
        return r.right

    def gambar_panel(self, layar, cepat, t):
        px = self.map_w
        pygame.draw.rect(layar, C["latar"], (px, 0, PANEL_W, self.h))
        pygame.draw.rect(layar, C["merah"], (px, 0, PANEL_W, 72))
        tulis(layar, "Damkar Surabaya", "judul", C["kartu"], px + 20, 14)
        tulis(layar, f"Simulasi respon kebakaran · {len(self.pos)} pos · {len(self.kec)} kecamatan", "kecil",
              (255, 218, 212), px + 20, 44)
        x0, lebar, y = px + 16, PANEL_W - 32, 86

        # waktu berangkat
        tulis(layar, "WAKTU BERANGKAT", "kecil", C["redup"], x0, y)
        y += 20
        x = x0
        for label, jam in [("Sekarang", None)] + [(f"{h:02d}:00", h * 60) for h in (7, 14, 17, 22)]:
            x = self.chip(layar, label, x, y, self.jam == jam, lambda j=jam: self.ganti_jam(j)) + 6
        y += 38
        dasar = datetime.now(WIB).hour * 60 if self.jam is None else self.jam
        x = self.chip(layar, "- 1 jam", x0, y, False, lambda: self.ganti_jam((dasar - 60) % 1440)) + 6
        x = self.chip(layar, "+ 1 jam", x, y, False, lambda: self.ganti_jam((dasar + 60) % 1440)) + 12
        hari = HARI[datetime.now(WIB).weekday()]
        tulis(layar, "Lalu lintas saat ini" if self.jam is None else f"Pola historis {hari} {jam_teks(self.jam)}",
              "tebal", C["biru"], x, y + 6, x0 + lebar - x)
        y += 38

        # kotak ketik jam (24 jam, HH:MM)
        tulis(layar, "Ketik jam (24 jam):", "biasa", C["teks"], x0, y + 6)
        kotak = pygame.Rect(x0 + 140, y, 80, 30)
        pygame.draw.rect(layar, C["kartu"], kotak, border_radius=8)
        garis = C["merah"] if self.galat_jam else C["biru"] if self.fokus_jam else C["garis"]
        pygame.draw.rect(layar, garis, kotak, 2 if self.fokus_jam or self.galat_jam else 1, border_radius=8)
        if self.ketik_jam or self.fokus_jam:
            w = tulis(layar, self.ketik_jam, "biasa", C["teks"], kotak.x + 10, kotak.y + 6)
            if self.fokus_jam and t % 1000 < 550:   # kursor berkedip
                pygame.draw.line(layar, C["teks"], (kotak.x + 12 + w, kotak.y + 6), (kotak.x + 12 + w, kotak.y + 23), 1)
        else:
            tulis(layar, "HH:MM", "biasa", (150, 154, 160), kotak.x + 10, kotak.y + 6)
        self.klik.append((kotak, lambda: setattr(self, "fokus_jam", True)))
        tulis(layar, "Format salah, contoh 06:30" if self.galat_jam else "Enter = terapkan", "kecil",
              C["merah"] if self.galat_jam else C["redup"], kotak.right + 10, y + 9)
        y += 46

        if not self.lokasi:   # keadaan awal: petunjuk
            kartu(layar, (x0, y, lebar, 150))
            lingkar(layar, (253, 237, 232), (x0 + 36, y + 38), 20)
            lingkar(layar, C["merah"], (x0 + 36, y + 38), 9)
            lingkar(layar, C["jingga"], (x0 + 36, y + 38), 5)
            tulis(layar, "Pilih lokasi kebakaran", "sedang", C["teks"], x0 + 68, y + 18)
            tulis(layar, "untuk melihat pos tercepat", "kecil", C["redup"], x0 + 68, y + 40)
            for i, baris in enumerate(("1.  Klik titik mana pun di peta, atau",
                                       "2.  Ketik nama tempat di kotak pencarian",
                                       "     (atau koordinat \"lat, lon\"), lalu Enter")):
                tulis(layar, baris, "biasa", C["teks"], x0 + 18, y + 74 + i * 21)
        else:
            self.gambar_hasil(layar, cepat, t, x0, y, lebar)
        if self.h > 700:
            tulis(layar, "Drag: geser peta  ·  Scroll: zoom  ·  Panah atas/bawah: ganti jam", "kecil", C["redup"],
                  px + PANEL_W // 2, self.h - 24, rata="tengah")

    def gambar_hasil(self, layar, cepat, t, x0, y, lebar):
        lon, lat, label, kec = self.lokasi
        # lokasi
        kartu(layar, (x0, y, lebar, 62))
        lingkar(layar, C["merah"], (x0 + 22, y + 31), 9)
        lingkar(layar, C["jingga"], (x0 + 22, y + 31), 5)
        tulis(layar, label, "tebal", C["teks"], x0 + 42, y + 12, lebar - 56)
        tulis(layar, f"Kecamatan {kec}  ·  {lat:.5f}, {lon:.5f}", "kecil", C["redup"], x0 + 42, y + 33, lebar - 56)
        y += 72

        # pos tercepat
        kartu(layar, (x0, y, lebar, 128))
        tulis(layar, "POS TERCEPAT TIBA", "kecil", C["redup"], x0 + 16, y + 12)
        selesai = sum(k["menit"] is not None or bool(k["galat"]) for k in self.kandidat)
        if cepat:
            ok = cepat["menit"] <= BATAS_TEMPUH_MENIT
            warna = C["hijau"] if ok else C["merah"]
            tulis(layar, cepat["pos"]["nama"], "sedang", C["teks"], x0 + 16, y + 30, lebar - 32)
            w = tulis(layar, f"{cepat['menit']:.1f}", "angka", warna, x0 + 16, y + 52)
            tulis(layar, "menit", "biasa", C["redup"], x0 + 22 + w, y + 78)
            tulis(layar, f"{cepat['km']:.1f} km lewat jalan", "kecil", C["redup"], x0 + 16, y + 102)
            teks = f"Memenuhi batas {BATAS_TEMPUH_MENIT} menit" if ok else f"Melewati batas {BATAS_TEMPUH_MENIT} menit"
            lw = F["kecil"].size(teks)[0] + 20
            pygame.draw.rect(layar, (230, 244, 234) if ok else (252, 232, 230), (x0 + lebar - lw - 14, y + 70, lw, 26), border_radius=13)
            tulis(layar, teks, "kecil", warna, x0 + lebar - 14 - lw // 2, y + 76, rata="tengah")
        elif KEY and selesai < len(self.kandidat):
            putar(layar, (x0 + 30, y + 72), t)
            tulis(layar, "Menghitung rute...", "sedang", C["teks"], x0 + 52, y + 54)
            tulis(layar, f"{selesai} dari {len(self.kandidat)} pos selesai", "kecil", C["redup"], x0 + 52, y + 78)
        else:
            tulis(layar, "Rute tidak tersedia" if KEY else "Butuh API key TomTom untuk waktu tempuh", "biasa", C["merah"], x0 + 16, y + 58, lebar - 32)
        y += 136
        dekat = self.kandidat[0]
        if cepat and cepat is not dekat:
            tulis(layar, f"Pos terdekat garis lurus ({dekat['pos']['nama']}, {dekat['lurus']:.2f} km) bukan yang tercepat",
                  "kecil", (176, 96, 0), x0 + 4, y, lebar - 8)
        else:
            tulis(layar, f"Pos terdekat garis lurus: {dekat['pos']['nama']} ({dekat['lurus']:.2f} km)", "kecil", C["redup"], x0 + 4, y, lebar - 8)
        y += 28

        # perbandingan
        tulis(layar, f"PERBANDINGAN {len(self.kandidat)} POS TERDEKAT", "kecil", C["redup"], x0, y)
        y += 20
        kartu(layar, (x0, y, lebar, len(self.kandidat) * 44 + 10))
        maks = max((k["menit"] or 0 for k in self.kandidat), default=0) or 1
        for i, k in enumerate(sorted(self.kandidat, key=lambda k: (k["menit"] is None, k["menit"] or 0, k["lurus"]))):
            ry = y + 6 + i * 44
            utama = k is cepat
            lingkar(layar, C["hijau"] if utama else (232, 234, 237), (x0 + 24, ry + 21), 11)
            tulis(layar, str(i + 1), "kecil", C["kartu"] if utama else C["redup"], x0 + 24, ry + 14, rata="tengah")
            tulis(layar, k["pos"]["nama"], "tebal" if utama else "biasa", C["teks"], x0 + 44, ry + 4, lebar - 140)
            rute_km = f"  ·  {k['km']:.1f} km rute" if k["km"] is not None else ""
            tulis(layar, f"{k['lurus']:.2f} km lurus{rute_km}", "kecil", C["redup"], x0 + 44, ry + 23)
            kanan = x0 + lebar - 14
            if k["menit"] is not None:
                warna = C["hijau"] if k["menit"] <= BATAS_TEMPUH_MENIT else C["teks"]
                tulis(layar, f"{k['menit']:.1f} mnt", "tebal", warna, kanan, ry + 4, rata="kanan")
                pygame.draw.rect(layar, (232, 234, 237), (kanan - 80, ry + 29, 80, 5), border_radius=3)
                pygame.draw.rect(layar, C["hijau"] if utama else (154, 160, 166),
                                 (kanan - 80, ry + 29, max(5, 80 * k["menit"] / maks), 5), border_radius=3)
            elif k["galat"]:
                tulis(layar, "gagal", "tebal", C["merah"], kanan, ry + 12, rata="kanan")
            elif KEY:
                putar(layar, (kanan - 10, ry + 21), t, 8)
        galat = next((k["galat"] for k in self.kandidat if k["galat"]), None)
        if galat:
            tulis(layar, galat, "kecil", C["merah"], x0 + 4, y + len(self.kandidat) * 44 + 18, lebar - 8)

    # --- event ---
    def hapus_cari(self):
        self.teks, self.hasil = "", []
        if self.pesan.startswith(("Pencarian", '"')):
            self.pesan = ""

    def zoom_ke(self, dz, x, y):
        """Zoom dengan titik layar (x, y) tetap di tempat."""
        if 10 <= self.zoom + dz <= 18:
            lon, lat = self.ke_lonlat(x, y)
            self.zoom += dz
            mx, my = merc(lon, lat)
            self.cx, self.cy = mx - (x - self.map_w / 2) / self.skala(), my - (y - self.h / 2) / self.skala()

    def event(self, e):
        if e.type == pygame.TEXTINPUT and self.fokus_jam:
            self.ketik_jam = (self.ketik_jam + "".join(c for c in e.text if c.isdigit() or c == ":"))[:5]
            self.galat_jam = False
        elif e.type == pygame.TEXTINPUT:
            self.teks += e.text
            self.hasil = []
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_BACKSPACE and self.fokus_jam:
                self.ketik_jam, self.galat_jam = self.ketik_jam[:-1], False
            elif e.key == pygame.K_BACKSPACE:
                self.teks = self.teks[:-1]
            elif e.key == pygame.K_ESCAPE:
                self.fokus_jam = False
                self.hapus_cari()
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.terapkan_jam() if self.fokus_jam else self.cari()
            elif e.key in (pygame.K_UP, pygame.K_DOWN):
                d = 60 if e.key == pygame.K_UP else -60
                self.ganti_jam(((datetime.now(WIB).hour * 60 if self.jam is None else self.jam) + d) % 1440)
        elif e.type == pygame.MOUSEWHEEL:
            x, y = pygame.mouse.get_pos()
            if x < self.map_w:
                self.zoom_ke(1 if e.y > 0 else -1, x, y)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            di_ui = e.pos[0] >= self.map_w or any(r.collidepoint(e.pos) for r, _ in self.klik)
            self.tekan, self.geser = ("ui" if di_ui else "peta"), 0
        elif e.type == pygame.MOUSEMOTION and e.buttons[0] and self.tekan == "peta":
            self.geser += abs(e.rel[0]) + abs(e.rel[1])
            self.cx -= e.rel[0] / self.skala()
            self.cy -= e.rel[1] / self.skala()
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self.tekan:
            tekan, self.tekan = self.tekan, None
            self.fokus_jam = False   # klik di luar kotak jam melepas fokus; kotak jam menyalakannya lagi lewat fn
            fn = next((fn for r, fn in reversed(self.klik) if r.collidepoint(e.pos)), None)
            if fn:
                fn()
            elif tekan == "peta" and self.geser < 5 and e.pos[0] < self.map_w:   # klik tanpa menggeser = pilih lokasi
                self.hapus_cari()
                self.pilih(*self.ke_lonlat(*e.pos))


def main():
    pygame.init()
    layar = pygame.display.set_mode((1280, 760), pygame.RESIZABLE)
    pygame.display.set_caption("Simulasi Respon Damkar Surabaya")
    pygame.key.set_repeat(400, 35)   # tahan Backspace untuk menghapus
    app, jam = App(), pygame.time.Clock()
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return pygame.quit()
            app.event(e)
        app.gambar(layar)
        pygame.display.flip()
        jam.tick(30)


if __name__ == "__main__":
    main()
