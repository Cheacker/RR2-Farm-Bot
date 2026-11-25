# RR2 Farm Bot — İnceleme ve Yol Haritası

İlk sürüm 2026-11-21. Aynı gün LDPlayer'da canlı keşifle güncellendi (oyun sürümü 12.9.0).

Hedef: kısa farm döngüsü + **24 saatten uzun, gözetimsiz** çalışma + kullanıcının her ekranı elle
test etmek zorunda kalmaması. Bu sağlandıktan sonra: açılıp kapatılabilen, seçici **hazine toplama** modu.

---

## 1. Ölçümler

### 1.1 ADB ve görüntü işleme maliyetleri (LDPlayer 9, 1600x900)

| İşlem | Süre | Not |
|---|---|---|
| `screencap -p` (botun şu anki yolu) | **~1.2 sn / kare** | Cihazda PNG kodlama + aktarım |
| `screencap` ham (RGBA, `-p`'siz) | ~0.5 sn / kare | Kolay ara adım |
| `input tap` (tek ADB çağrısı) | **~234 ms / dokunuş** | Her çağrı cihazda bir Java süreci açıyor |
| 5 `input tap` tek shell'de | ~976 ms | Toplu göndermek kurtarmıyor |
| `sendevent` ile dokunuş | ~359 ms | Daha hızlı değil; üstelik dokunuşu kaydırma gibi algıladı |
| Tesseract OCR, tek bölge | ~295 ms | Her çağrı ayrı `tesseract.exe` süreci |
| Tam ekran şablon eşleme | ~333 ms | |
| Bölgeli şablon eşleme | ~6 ms | |

Sonuç: botu yavaşlatan şey sırasıyla **ekran alma gecikmesi (1.2 sn)**, **dokunuş gecikmesi (234 ms)**,
**OCR ve bölgesiz arama**. Örnek: kupa filtresi ayarı 30 dokunuş × (234 + 150 ms bekleme) ≈ 11.5 sn.

### 1.2 Denenen ve elenen "process listener" yolları

- **logcat:** oyun Unity değil, keen games'in kendi motoru (`com.keengames.gameframework`); ekran/maç durumu loglamıyor.
- **Firebase Analytics olayları** (`setprop log.tag.FA VERBOSE`): yerel olarak görünür hâle getirildi ama
  maç boyunca yalnızca reklam olayı (`ad_query`) geldi; maç başı/sonu olayı yok. **Bu yol kapalı.**
- Ağ dinleme ve bellek okuma ban riski nedeniyle önerilmiyor.

→ Ekran durumu yine görüntüden okunacak; çözüm ekran almayı ve dokunmayı hızlandırmak (bkz. 3.2).

### 1.3 Mevcut şablonlar güncel sürümde çalışıyor

HEAD'deki şablonlar 12.9.0'da yüksek skorla tutuyor: `icon_forge` 1.00, `icon_trophy` 0.99, `btn_start_search` 0.99,
`area_top_opponent` 0.93, `btn_attack_start` 0.97, `btn_green_back` 0.99, `btn_close` 1.00, `btn_archer` 0.99,
`btn_collect_league` 1.00. Yani "tam ekran şablon" dönemi doğru çalışıyordu; sorun yalnızca hızdı.
Doğru düzeltme OCR'a geçmek değil, **aynı şablonları bölgeyle sınırlamak** (333 ms → 6 ms).
Commitlenmemiş OCR'lı sürüm `wip/ocr-regions` dalında saklandı; master HEAD'e döndü.

Dikkat: sarı butonlar birbirine benziyor. Örneğin maç sonu "SKIP" butonu `btn_collect_league` ile 0.83 skor veriyor.
Her şablon kendi bölgesinde ve kendi ekran imzasıyla birlikte aranmalı.

---

## 2. Keşif bulguları (canlı oyun)

### 2.1 Ekranlar ve akış

| Ekran | Tanıma | Not |
|---|---|---|
| Zorunlu güncelleme ("Download now!") | yükleme ekranında popup | Play Store'a gidiyor (`com.android.vending` ön planda) → "Update" → sürüm değişince oyunu yeniden aç. **24 saatlik çalışmayı durdurur; bot otomatik yapmalı.** |
| Oyun içi indirme ("Downloading… %") | yükleme ekranı | Güncelleme sonrası; beklenmeli, yeniden başlatılmamalı |
| Açılış popup zinciri | `btn_close` (1524,88) | "You have been attacked", "Webstore Gem Sale", "Webstore Exclusive Offer", "Special offer" → hepsinin çarpısı aynı yerde |
| Lig sonucu ("JOIN LEAGUE!") | `btn_collect_league` | Çarpı yok, butona basılmalı |
| Break Time | metin/şablon | ~5 saat oyundan sonra, ~15 dk (kullanıcı bilgisi) |
| Conquest / klan hediyesi | toplama butonu | Ayda bir; çarpı değil toplama butonu |
| HOME | `icon_forge` / `icon_trophy` | Sağ altta büyük çapraz kılıç = "Prepare for Battle" (kupaya yakın rastgele rakip) |
| Global Leaderboard | `area_top_opponent` | Sağ/sol oklar yalnızca Global ↔ Alliances arasında geçiyor |
| Advanced Search | `btn_start_search` | Kupa aralığı + "Currently online" vb. filtreler |
| Prepare for Battle | `btn_attack_start` + `btn_green_back` | **Mini harita** (yol + binalar), dişli setleri A–F, "NEW ENEMY" 4.500 altın, "ATTACK!" ekmek |
| Maç içi | `btn_archer` | ~1:40 süre; sağ üstte **15 elmaslık** iki güçlendirme (9 ve 0 tuşları) |
| Maç sonu | "SKIP" → birkaç sn sonra "CONTINUE" | Kayıpta sandık yok |

Teklif pencerelerinde Türkçe butonlar görüldü ("Hadi bak!", "Teklifi incele"). Cihazın birincil dili zaten `en-US`
idi ama ikinci dil olarak `tr-TR` tanımlıydı; 2026-11-21'de kaldırıldı (`settings put system system_locales en-US`
+ yeniden başlatma, etkin yapılandırma artık yalnızca `en-rUS`). Teklifler yine Türkçe gelirse kaynak hesap/sunucu
tarafıdır; şablon tabanlı tespit bundan etkilenmez.

### 2.2 Maç içi

- HEAD'de `IN_GAME_TAP_COORDS = (10, 10)`: **kral hiç yürümüyor**, maçı birlikler kazanıyor (600 kupada yeterli).
  Kaydedilen videoda kral başlangıç noktasında kaldı.
- Oyun **klavye kısayollarını** ekranda gösteriyor: 1-2-3 birlikler, 5-6-7 büyüler, Q/W yetenekler, 9/0 elmaslı
  güçlendirmeler. `input keyevent` ile çalışıyor (W denendi, yetenek tetiklendi). Birlik/büyü çağırmak için
  koordinat yerine tuş kullanılabilir: ekran düzeni değişse de bozulmaz.
  **9 ve 0 asla gönderilmemeli** (15'er elmas).
- Yön tuşları kralı yürütmüyor; kral yalnızca dokunuşla hareket ediyor.
- Kamera kralı takip ediyor; sabit bir ekran noktasına basmak "kralın göreli olarak şu yönüne git" demek.
  Yol döndüğünde sabit nokta yolun dışına düşüyor; kralın "pek kıpırdamaması"nın sebebi bu.

### 2.3 Elmas harcatabilecek bölgeler (kör dokunuş yasak)

Maç içi (1300,110) ve (1490,110) güçlendirmeleri, ölüm ekranında "RESURRECT 36", Shop'ta Worker satın alma
`(140–380, 730–795)`, "NEW ENEMY" (4.500 altın). Kurtarma merdiveni bu bölgelere hiçbir zaman kör basmamalı.

### 2.4 Rakip isimleri

Düşük kupalı listede isimlerin çoğu Vietnamca karakterli. Kullanıcının saha gözlemi: Tesseract bunları doğru
okuyamasa da **tutarlı** okuyor; yanlış eşleşme ya da eşleşmeme görülmemiş. Yani doğruluk sorunu yok, yalnızca
maliyet var (isim başına ~295 ms, ekranda 6 isim → ~1.8 sn). **Öneri (verimlilik için):** isim bölgesinin
görüntü özetini (perceptual hash) anahtar yapmak, ~1 ms. Geçişte eski JSON kayıtları atılabilir (15 dk'lık
"aktif" işaretleri zaten kısa ömürlü).

---

## 3. Hedef mimari

```
 FrameSource ──► ScreenClassifier ──► Policy (ekran → eylem) ──► Input
 (scrcpy akışı)   (screens.yaml,        (sabit dokunuş makroları,   (scrcpy kontrol kanalı;
                   bölgeli şablon,       her makro sonunda teyit)    birlik/büyü için tuş)
                   ~10 ms, OCR yok)
                         │
                         └─ tanınmayan → kurtarma merdiveni + unknown/'a kaydet

 Supervisor (ayrı süreç): botu başlatır, heartbeat'i izler, asılırsa yeniden başlatır,
 Break Time / güncelleme beklemelerini yönetir, günlük rapor üretir.
```

### 3.1 Algıla → karar ver

Her karede "bu hangi ekran?" sorulur; `self.state` tahmin değil gözlem olur. RESYNC, `GOING_BACK` ve miss
sayaçlarının çoğu kalkar. Sabit dokunuşlar korunur, kural şu: **ekranı teyit et → dokunuş dizisini kör bas →
sonraki ekranı teyit et.** Sabit `sleep`'ler yerine `wait_for(ekranlar, timeout)`.

### 3.2 Hız: scrcpy

scrcpy sunucusu cihazda tek süreç olarak kalır; video akışı (son kare her an bellekte, ~0 ms) ve kontrol kanalı
(dokunuş/tuş ~1-5 ms, süreç açmadan) verir. Ölçülen 1.2 sn/kare ve 234 ms/dokunuş birlikte kalkar.
Bir maç döngüsünde ~15 kare + ~40 dokunuş varsayımıyla **maç başına ~25 sn** tasarruf beklenir; Faz 2'de ölçülecek.
Gereken: `scrcpy-server` jar (scrcpy sürümünden) + Python istemcisi; ffmpeg makinede mevcut.

### 3.3 Kurtarma merdiveni

Bilinmeyen ekran süresi arttıkça: bilinen çarpılar (1524,88) → bilinen toplama butonları → Android BACK →
oyunu yeniden başlat → LDPlayer'ı yeniden başlat. Hiçbir basamak 2.3'teki bölgelere dokunmaz.
Tanınmayan kareler tekilleştirilip `unknown/`'a kaydedilir; ben etiketleyip imza ve test eklerim.

### 3.4 Kayıt ve test

- `matches.csv` (her maç: süre, sonuç, kupa/altın/inci farkı), `events.jsonl` (ekran geçişleri, kurtarmalar).
- Oyuncu DB'si SQLite'a (atomik, budanabilir), anahtar görüntü özeti.
- `tests/screens/<ekran>/*.png` etiketli kareler; `uv run pytest` emülatörsüz doğrular. Bu oturumda toplanan
  ~30 kare (popup zinciri, güncelleme, leaderboard, search, prepare, maç, sonuç) ilk test seti olur.

---

## 4. Fazlar

### Faz 0 — Sağlamlık ve ölçüm (HEAD üzerine) — kod tamam (2026-11-22), 24 saatlik koşu bekliyor
Ayrıntı: `CHANGELOG.md` 0.2.0 ve `HANDOVER.md`.
- Şablon aramalarını bölgeyle sınırla (OCR'a geçmeden).
- Yeni ekranlar: zorunlu güncelleme (Play Store akışı dahil), oyun içi indirme beklemesi, açılış popup zinciri,
  JOIN LEAGUE, RETREAT (kahraman öldü), Shop'tan çıkış, Break Time (~15 dk bekle, tekrar dene).
- Hatalar: eksik şablonlar (`btn_take_a_break`, `btn_king_claim`, `btn_archer_boosted`), `bot.py:240` yazım hatası,
  sıfıra bölme, ADB/`ldconsole` çağrılarına timeout.
- Heartbeat + supervisor betiği; dosya log + `matches.csv`.
- **Çıktı:** 24 saatlik koşu ve rapor (maç/saat, inci/saat, kurtarma sayısı, Break Time sayısı).

### Faz 1 — Ekran atlası ve sınıflandırıcı
- `screens.yaml` + `ScreenClassifier` + regresyon testleri (bu oturumun kareleri + `fail_debug/`).
- Önce gölge mod: eski mantık çalışır, sınıflandırıcı yanında anlaşmazlıkları loglar.

### Faz 2 — Hız
- scrcpy akışı + kontrol kanalı; birlik/büyü çağrıları tuşla.
- Rakip ismi OCR'ı yerine görüntü özeti.
- **Ölçüt:** ortalama döngü süresi, Faz 0 ölçümüne karşı.

### Faz 3 — Algıla/karar ver döngüsüne geçiş
- Ekran ekran: popup'lar → COF → HOME → TROPHY_MENU → FILTERED_RANKS → ATTACK_PREP → GAME_LOAD → IN_GAME.
- Her adımda 24 saatlik koşu tekrarlanır.

### Faz 4 — Hazine modu (24 saat kanıtlandıktan sonra, aç/kapa)

---

## 5. Hazine modu

### 5.1 Bilinenler (kullanıcıdan)
- Hazineler binalar yıkılınca **yola, binanın önüne** düşüyor; ne zaman düşeceği belli değil. Yer
  değiştirmiyorlar ama **olduğu yerde dönüyorlar (3D modeller)**: görünüşleri kareden kareye değişir.
- **Kral üzerinden geçince** toplanıyor (dokunmak gerekmiyor).
- Düşen türler: **elmas, tokenler (~7 çeşit), inciler, mamalar, rünler (4 çeşit)**. Değerli olan genelde elmas, ama
  kullanıcı hangi türleri toplayacağını **kendisi seçebilmeli** → ~14 sınıflı bir tespit problemi.
- Hazine maçları 600 kupada değil; oyuncunun kupası düşük tutulur (~2000) ve rakip ona yakın olur.
- Asıl hedef: kullanıcının **favorilere kaydettiği hazine üsleri** (çok kuleli, kolay).
- Favorilere giriş: HOME'da saldırı butonunun üstündeki 5 butondan sağ üstten 2.si (iki gülümseyen yüz, yeşil
  etiket, oyun kolu ikonu) → açılan menüde "Favorites".
- Hazine maçlarında **birlikler kullanılabilir**.

### 5.2 Neden zor ve nasıl kolaylaşıyor
Asıl zorluk tespit değil, **kralı yolda yürütmek**: hazineler yolun üstünde olduğuna göre yolu baştan sona yürüyen
kral çoğunu zaten toplar. Seçicilik ancak sapma/bekleme gerektiğinde devreye girer.

1. **Rota, maç öncesi mini haritadan:** "Prepare for Battle" ekranındaki mini harita yolu (bej) ve binaları
   üstten gösteriyor. Buradan yol iskeleti ve "yola bitişik binalar" (olası düşme noktaları) çıkarılır.
   Favori üsler sabit olduğundan rota üs başına bir kez çıkarılıp saklanabilir.
2. **Maç içinde yol takibi:** kamera kralı ortalıyor. Kralın çevresinde yol rengi bölütlenir, rotadaki bir sonraki
   noktaya doğru yoldaki bir noktaya dokunulur ya da basılı tutulur. Kral takılırsa (kare farkı ~0) alternatif nokta denenir.
3. **Hazine tespiti:** nesneler döndüğü için tek bir şablon yetmez. İki ipucu birlikte kullanılır:
   - *Nerede:* yerinde dönen nesne, ardışık karelerde **sabit bir noktada sürekli değişen küçük bir bölge**
     üretir. Kamera kralı takip ettiği için önce kareler hizalanır (kral merkezli kaydırma), sonra bu
     "yerinde dönen" lekeler bulunur. Renk filtresi (elmas pembesi, token/rün renkleri) adayları daraltır.
   - *Ne:* aday bölge, her türün birkaç dönme açısından alınmış örnekleriyle karşılaştırılır (tür başına
     8-12 kırpıntı) ya da küçük bir sınıflandırıcıya verilir. ~14 sınıfta şablon seti yetmezse YOLO-nano
     (CPU'da ~10-30 ms) eğitilir. Veri, normal koşularda maç içi kayıtlardan gelir.
4. **Seçici politika:** kullanıcının seçtiği tür + rotadan sapma mesafesi + kalan süre → sap, üstünden geç, rotaya dön.
   Seçilmeyen türler için sapma yapılmaz (yol üstündeyse zaten toplanır).
5. **Akış farkı:** HOME → favoriler → üs seç → saldır. Birlik/büyü çağrıları tuşla. Maç sonu aynı.
6. **Değerlendirme:** aç/kapa A/B; ölçüt saat başı elmas (ve seçilen diğer türler).

### 5.3 Katalog nasıl çıkarılacak
Birkaç hazine maçı `screenrecord` ile kaydedilir (45 fps). Kareleri tek tek incelemek yerine kod, ardışık kareler
arasında yeni beliren ve yerinde dönen nesneleri bulur; yalnızca o anların karelerini ve kırpıntılarını inceleyip
türlere ayırırım. Her tür için farklı dönme açılarından örnekler `En_Templates/treasure_*` olur.

### 5.4 Exploration log (2026-11-24, set B "treasure hunter", trophies ~2150)

- **Favorites path:** HOME (1535,505) → Community. Card swipes stop at varying positions, so the robust way is:
  Leaderboard card OPEN → right arrow (1545,520) → "Favorite Players". Rows: swords button at x≈1415; inactive
  players have gray swords. Insta-Troops page has a **SKIP 25 gems** button at (800,795): never tap there.
- **Prep screen:** set B selected by the user; attack costs 11 food.
- **Death screen danger:** a blind troop tap at (470,720) hit RESURRECT → "Are you sure? CONFIRM 36 gems".
  Nothing was spent (CANCEL), but in matches use **hotkeys only** (keyevent 8 9 10 = troops 1-3, 12 13 14 = spells
  5-7, 45/51 = Q/W) and keep any screen holds inside x 550-1050 / y 150-600.
- **Sister Maria (1827):** king died in 15 s (mummies). User: don't attack it again, nor any base where we died.
- **affreux 02 (1577), 3 runs** with hotkey spam plus different king inputs (hold up / steering / tap-drag-hold
  experiment): every run ended at "SIEGE!" (time out) with **28-33 % progress, 1 crown, -11..-13 trophies**.
  The input type made no measurable difference; the camera jumps ~200 px at a time (dead-zone camera), so camera
  shift is not a usable king-movement signal. **Open question for the user: how is the king moved?**
- **Treasures are reported on the result screen** under "Hidden Treasures:" with type and level (run 3: two runes,
  "Cannon: Swift Shells", "Increased Normal Damage"). This gives a per-match ground truth for evaluation.
- One uncollected treasure seen on the final frame of run 2: a red/pink gem at (1290,245), off the king's path.

### 5.5 King movement solved (2026-11-25)

- **`input tap` / `input swipe` do not steer the king reliably.** A real finger via `sendevent` on LDPlayer's
  multitouch device (`/dev/input/event2`, 1600x900, slot 0, tracking id, BTN_TOUCH) does: the king walks toward
  the finger and the target updates while it is held (user: real taps work, the bot's very short taps stop early).
  Prototype: `dev_tools/treasure/touch.py` (one persistent `adb shell`; keys via the same device, 9/0 not mapped).
- **Progress signal:** the king icon on the top bar (y≈33, x≈440→1140) moves right as he advances along the path.
  Holding "up"/"right" moved it forward, "left"/"down" moved it **back**, so it is a reliable 1-D feedback.
- **Path follower** (`play5.py`): finger stays down; every screenshot (~1.5 s) compare icon x; keep heading while it
  advances, else try neighbouring headings (±45°…, reversing last). No map knowledge needed.
  - 1392-trophy base: 98 %, 2 crowns, castle reached in 51 s, one heading change.
  - affreux 02 (1546): castle reached in 64 s (vs 28-33 % with the old inputs), but the king **died at the castle**
    at 72 s: at the path end the follower kept turning (0°, 315°) instead of staying. Fix: stop steering at the end.
- Hidden Treasures on the result screen: Sweireid (1392) gave 1 rune ("Sonic Blast: Sound Barrier").
- **End-of-path fix:** when the icon reaches x ≥ 1120 (castle), lift the finger and only cast spells. VN - Ngoc Ky Lan
  (1918, many towers): castle at 54 s, **100 %, 3 crowns, 80 s**, Hidden Treasures: +320 pet food, 2 runes
  ("Gargoyle: Carnage", "Skull Tower: Skeleton Bombs"). Two heading changes in the whole match.
- **Next:** move `Touch` into `src/controller.py`, add a treasure-mode state flow (HOME → Community → Leaderboard →
  Favorites → base list, skipping death bases) and the follower as its IN_GAME handler; read Hidden Treasures from
  the result screen into `logs/treasures.csv`. Selective pick-up (detours to off-path drops) comes after that.
