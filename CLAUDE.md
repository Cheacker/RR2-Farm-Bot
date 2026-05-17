# Royal Revolt 2 Farm Bot — Tam Proje Belgesi

## Genel Bakış

MEmu emülatöründe çalışan Royal Revolt 2 için ADB tabanlı farm botu.
Bağlantı: `adbutils` → `127.0.0.1:21503`. Görüntü: OpenCV `TM_CCOEFF_NORMED`.
Çalıştırma: `cd rr2-farm-bot-public/src && python bot.py [port] [trophy_filter]`

---

## Dosya Yapısı

```
rr2-farm-bot-public/
  src/
    bot.py          — Ana bot, state machine, tüm handle_* fonksiyonları
    controller.py   — ADB bağlantısı, screencap, tap/swipe/hold
    vision.py       — Template matching, OCR (Tesseract)
    player_db.py    — Aktif oyuncu takibi (JSON tabanlı)
  En_Templates/     — Tüm .png template dosyaları
  get_coords.py     — Koordinat bulma aracı (emülatörde tıkla → koordinat yaz)
  recrop.py         — Template kırpma aracı (menülü)
  fail_debug/       — Hata anında kaydedilen ekran görüntüleri
```

---

## State Machine

```
HOME → TROPHY_MENU → FILTERED_RANKS → ATTACK_PREP → GAME_LOAD → IN_GAME → CHAMBER_OF_FORTUNE → HOME
                                            ↓
                                       GOING_BACK → FILTERED_RANKS
```

### State Geçiş Koşulları

| State | Geçiş Tetikleyicisi |
|---|---|
| HOME → TROPHY_MENU | `icon_forge` bulundu |
| TROPHY_MENU → FILTERED_RANKS | `btn_start_search` (sarı) bulundu ve basıldı |
| FILTERED_RANKS → ATTACK_PREP | Kılıç ikonu tıklandı |
| ATTACK_PREP → GAME_LOAD | `btn_attack_start` bulundu ve basıldı |
| GAME_LOAD → IN_GAME | `btn_archer` görüldü |
| GAME_LOAD → GOING_BACK | `btn_bring_me_back` görüldü (aktif oyuncu) |
| GOING_BACK → FILTERED_RANKS | Her zaman (geri butonuna basar) |
| IN_GAME → COF | `btn_continue` görüldü |
| COF → HOME | `_cof_tap_home()` çağrıldı |

---

## Koordinatlar (`bot.py` sabitleri)

| Sabit | Değer | Açıklama |
|---|---|---|
| `TROPHY_COORDS` | `(1545, 128)` | Ana ekran kupa/dövüş ikonu |
| `COLLECT_ALL_RESOURCES` | `(60, 506)` | Ana ekran tüm kaynakları topla butonu |
| `BLUE_SEARCH_COORDS` | `(1436, 213)` | Kupa menüsü mavi arama butonu |
| `ARCHER_COORDS` | `(200, 800)` | Oyun başlangıcı okçu seçim butonu |
| `CANNON_COORDS` | `(240, 800)` | Oyun başlangıcı top seçim butonu (2x basılır) |
| `MINUS_LEFT_COORDS` | `(810, 226)` | Kupa filtresi sol eksi |
| `MINUS_RIGHT_COORDS` | `(1084, 226)` | Kupa filtresi sağ eksi |
| `PLUS_LEFT_COORDS` | `(985, 229)` | Kupa filtresi sol artı (şu an kullanılmıyor) |
| `PLUS_RIGHT_COORDS` | `(1258, 226)` | Kupa filtresi sağ artı |
| `IN_GAME_TAP_COORDS` | `(954, 302)` | Oyun içi her 0.65s'de tıklanan nokta |
| `GEAR_SET_3_COORDS` | `(366, 818)` | Saldırı hazırlık ekranı dişli seti 3 |
| `VIDEO_CLOSE_COORDS` | `(1522, 94)` | Video teklifi ve shop çarpı butonu |
| `SHOP_COORDS` | `(1540, 508)` | Ana ekrandaki dükkan butonu |

---

## Template Listesi (`En_Templates/`)

| Dosya | Threshold | Açıklama |
|---|---|---|
| `icon_forge.png` | 0.90 | Ana ekranda görünen dövüşhane ikonu (HOME tespiti için) |
| `btn_start_search.png` | 0.95 | Sarı arama butonu (rakip bulununca çıkar) |
| `area_top_opponent.png` | 0.92 | Kılıç/saldırı ikonu (ranked listede, multi-match) |
| `btn_attack_start.png` | 0.90 | Saldır butonu (maç hazırlık ekranı) |
| `btn_bring_me_back.png` | 0.90 | Aktif oyuncu uyarısı — geri dön butonu |
| `btn_archer.png` | 0.90 | Maç başladı, okçu seçim butonu |
| `btn_continue.png` | 0.95 | Maç sonu devam butonu (COF girişi) |
| `btn_give_up.png` | 0.70 | COF pes et butonu |
| `btn_sell.png` | 0.70 | COF sat butonu |
| `btn_melt.png` | 0.92 (loop1) / 0.70 (loop2) | COF erit butonu |
| `chest_1..6.png` | 0.70 | COF sandıkları (6 ayrı template, 3D perspektif) |
| `btn_close.png` | 0.57–0.80 | Genel çarpı/kapat butonu |
| `btn_collect.png` | 0.80 | Kaynak toplama butonu (HOME'da ara sıra çıkar) |
| `btn_big_collect.png` | 0.80 | Büyük toplama butonu (HOME'da) |
| `btn_video.png` | 0.90 | Video teklif popup butonu (ekmek bitince çıkar) |
| `btn_food.png` | 0.90 | Dükkanda ekmek satın al butonu |

**`SILENT_ON_MISS`** (`vision.py`): `btn_continue` ve `btn_melt` — her döngüde arandığı için bulunamadığında log basmaz.

---

## Handle Fonksiyonları

### `handle_home`
- Her iterasyonda `icon_forge` arar
- Bulunca: altın/inci OCR, her 3 maçta `COLLECT_ALL_RESOURCES` tap, kupa menüsüne geç
- Bulunamazsa: `_trophy_miss_count` artar
  - Her 2'de: `(10,10)` tap (popup kapatma denemesi)
  - Her 6'da: `btn_close` (threshold 0.57) + `btn_big_collect`
  - Her 5'te: `btn_collect` varsa bas + 3.5s bekle; yoksa `btn_close`
  - 21'den sonra: oyunu yeniden başlat

### `handle_trophy_menu`
- `btn_start_search` (sarı) görünürse: OCR ile sol/sağ kupa değerini oku, filtre ayarla, bas → FILTERED_RANKS
- Görünmezse: `BLUE_SEARCH_COORDS`'a spam tap

**Filtre mantığı:**
- Sol: değer 300'den büyükse fazlayı eksi ile düşür
- Sağ: `_trophy_filter` (varsayılan 600) hedefi, delta kadar +/- bas

### `handle_filtered_ranks`
- `area_top_opponent` çoklu eşleştirme (threshold 0.92)
- Y koordinatına göre sırala (yukarıdan aşağı)
- Her kılıç için OCR ile isim oku
  - İsim okunamazsa: son kılıçsa → scroll, değilse atla
  - `db.is_active()` true ise atla, `_skip_top` artır
  - `_skip_top >= 4` ise liste scroll et (`_scroll_count` kadar)
- 27 bulunamazsa: oyunu yeniden başlat
- Her 3 bulunamazsa: scroll
- Her 9'da: `btn_attack_start` (threshold 0.5) varsa ATTACK_PREP

### `handle_attack_prep`
- `btn_attack_start` bulunca: önce `GEAR_SET_3_COORDS` tap (dişli seti seç), sonra saldır
- 12s içinde bulunamazsa: FILTERED_RANKS'e dön

### `handle_game_load`
Kontrol sırası (önemli):
1. **`btn_video`** — ekmek teklifi popup: `VIDEO_CLOSE_COORDS` tap → shop aç → `btn_food` varsa al → kapat → HOME
2. **`btn_bring_me_back`** — aktif oyuncu: kaydet, GOING_BACK
3. **`btn_archer`** — maç başladı: okçu+top tap, IN_GAME
4. Hiçbiri 15 kez bulunamazsa: oyunu yeniden başlat

### `handle_going_back`
- Her zaman `(1430, 85)` tap (yeşil geri butonu) → FILTERED_RANKS

### `handle_in_game`
- Her 0.65s'de `IN_GAME_TAP_COORDS` tap
- Ekran mevcutsa her 4s'de `btn_continue` kontrol
- 3 dakika timeout → oyunu yeniden başlat
- Ekran `None` olsa bile (ADB geçici hata) tap yapmaya devam eder

### `handle_chamber_of_fortune`
İki while döngüsü — ikisi de `self.running` kontrolü yapar:

**Döngü 1** (`_chest_taps < 3`):
- `btn_give_up` → pes et → HOME
- `btn_sell` → melt/sell kararı → devam et (chest loop'a dön)
- Sandık bul → tap → tap sayısı teyit
- 2 kez sandık bulunamazsa → HOME

**Döngü 2** (3 sandık açıldıktan sonra):
- `btn_give_up` → pes et → HOME
- `btn_sell` → melt/sell → HOME
- Sandık 0 → HOME

**Melt/Sell kararı:**
```python
gold_ref = self._gold_last if self._gold_last is not None else self._gold_start
if melt and (gold_ref is None or gold_ref > 22_000_000):
    # erit
else:
    # sat
```
`gold_ref` None ise (ilk maç, OCR henüz çalışmadı) → eriyor (güvenli taraf).

---

## ADB Bağlantı Yönetimi

### `controller.py` yöntemleri

| Metod | Ne yapar |
|---|---|
| `_connect()` | Gentle bağlantı — kill-server yok |
| `_reconnect()` | Aggressive — kill-server + start-server |
| `current_screen(retries=4)` | 4 deneme, başarısızsa `_reconnect()` çağırır |
| `quick_screen_check()` | Tek deneme, reconnect yok — MEmu başlatma polling için |

`screencap -p` shell komutuna `timeout=15` eklendi — ADB soket donmasında 2 dakikalık sessizliği önler.

### Ana döngüde ADB hata yönetimi

- `screen is None` → `_main_adb_fail` artar
- Her 5. denemede log basar
- IN_GAME state'deyse `handle_in_game(None)` çağırır (tap devam eder)
- 20 denemede → MEmu yeniden başlat

---

## MEmu Yeniden Başlatma Mantığı

`__init__`'te:
- ADB cihaz yoksa MEmu'yu başlat, `_memu_just_launched = True`

`loop()` başlangıcında:
- `_memu_just_launched` → DB'ye kaydet, oyunu başlat (MEmu'yu kapatma)
- `last_restart is None` → DB'ye kaydet, oyunu başlat
- `>= 3 saat` geçmişse → `_restart_memu()` (MEmu'yu kapat-aç)
- Diğer → sadece oyunu yeniden başlat

**`_memu_just_launched` neden var:** `__init__` MEmu'yu açtıktan sonra `loop()` 3 saati kontrol edip tekrar kapatmasını önler.

`_restart_memu()`'da polling: `quick_screen_check()` kullanır, `current_screen()` değil — aksi halde kill-server MEmu'nun ADB başlatmasını bozar.

---

## Player DB (`player_db.py`)

- `player_data.json` dosyasına yazar
- `mark_active(name)` → son saldırı zamanını kaydeder
- `is_active(name)` → son 3 saatte saldırıldıysa True döner (3x kılıç limiti)
- `info_str(name)` → log için "active: True (HH:MM:SS)" veya "active: False (never)"

---

## OCR Bölgeleri

| Ne | Koordinatlar (x1,y1,x2,y2) |
|---|---|
| Altın miktarı | `(102, 29, 253, 72)` |
| İnci miktarı | `(88, 194, 213, 228)` |
| Kupa filtresi sol değer | `(850, 212, 948, 250)` |
| Kupa filtresi sağ değer | `(1122, 209, 1218, 252)` |
| Oyuncu ismi | Kılıç merkezinden: offset `(-705, -20)`, boyut `525x40` |

---

## Recrop Menüsü (`recrop.py`)

| No | Açıklama | Template |
|---|---|---|
| 1 | Kupa ikonu | `icon_trophy` |
| 2 | Sarı ara butonu | `btn_start_search` |
| 3 | Kılıç/saldırı butonu | `area_top_opponent` |
| 4 | Saldırı başlat butonu | `btn_attack_start` |
| 5 | Atla butonu | `btn_skip` |
| 6 | Devam et butonu | `btn_continue` |
| 7 | Sat butonu | `btn_sell` |
| 8 | Pes et butonu | `btn_give_up` |
| 9 | Devam etmek için dokun | `text_tap_to_continue` |
| 10 | Sandıklar (6 ayrı) | `chest_1..6` |
| 11 | Mac yükleme ekranı | `screen_loading` |
| 12 | Beni geri götür butonu | `btn_go_back` |
| 13 | Yeşil geri butonu | `btn_green_back` |
| 14 | Archer butonu | `btn_archer` |
| 15 | Gri kılıç (saldırı limiti) | `area_top_opponent_gray` |
| 16 | Topla butonu | `btn_collect` |
| 17 | Çarpı/kapat butonu | `btn_close` |
| 22 | Video teklif butonu | `btn_video` |
| 23 | Ekmek satın al butonu | `btn_food` |

---

## Önemli Tasarım Kararları

- **Mavi buton template'i yok:** `btn_search_anim_1-8` kaldırıldı, sabit koordinat (`BLUE_SEARCH_COORDS`) kullanılıyor
- **COF inner loop'lar `self.running` kontrol eder:** Ctrl+C basıldığında sandık işleme devam etmez
- **Fatal hata yok:** Tüm hata noktaları (HOME 21 miss, FILTERED_RANKS 27 miss, IN_GAME 3 min, GAME_LOAD 15 miss) oyunu yeniden başlatır, botu kapatmaz
- **Gri kılıç ortada yanlış scroll:** Yalnızca listede SON rakibin ismi okunamazsa scroll — ortadaki gri kılıçlar false scroll tetiklemez
- **Timeout=15 screencap'te:** `device.shell("screencap -p", timeout=15)` — ADB soket donmasında sonsuz beklemeyi önler
- **`gold_ref` fallback zinciri:** `_gold_last` → `_gold_start` → None (None ise erit) — ilk maçta OCR henüz çalışmadan doğru karar

---

## Çalıştırma Örnekleri

```bash
python bot.py              # varsayılan port 21503, trophy filter 600
python bot.py 700          # trophy filter 700
python bot.py 21503 800    # explicit port + trophy filter 800
```
