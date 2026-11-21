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

Butonlardaki yazıların bir kısmı Türkçe ("Hadi bak!", "Teklifi incele"); README "oyun İngilizce olmalı" diyor ama
teklif pencereleri yerel dilde geliyor. **Metne dayalı (OCR) tespitin bir kırılganlığı daha.**

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

Düşük kupalı listede isimlerin çoğu Vietnamca karakterli rastgele hesaplar. Tesseract (eng) bunları bozuk okuyor;
`player_data.json`'ın 3.9 MB olmasının bir sebebi muhtemelen aynı oyuncunun farklı okunuşlarla tekrar tekrar
kaydedilmesi. **Öneri:** isim bölgesinin görüntü özetini (perceptual hash) anahtar olarak kullanmak. OCR yok,
dil bağımsız, ~1 ms.

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

### Faz 0 — Sağlamlık ve ölçüm (HEAD üzerine)
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
- Hazineler binalar yıkılınca **yola, binanın önüne** düşüyor; ne zaman düşeceği belli değil, **hareket etmiyorlar**.
- **Kral üzerinden geçince** toplanıyor (dokunmak gerekmiyor).
- Değerli olan genelde elmas, ama kullanıcı hangi türleri toplayacağını **kendisi seçebilmeli**.
- Hazine maçları 600 kupada değil; oyuncunun kupası düşük tutulur (~2000) ve rakip ona yakın olur.
- Asıl hedef: kullanıcının **favorilere kaydettiği hazine üsleri** (çok kuleli, kolay).

### 5.2 Neden zor ve nasıl kolaylaşıyor
Asıl zorluk tespit değil, **kralı yolda yürütmek**: hazineler yolun üstünde olduğuna göre yolu baştan sona yürüyen
kral çoğunu zaten toplar. Seçicilik ancak sapma/bekleme gerektiğinde devreye girer.

1. **Rota, maç öncesi mini haritadan:** "Prepare for Battle" ekranındaki mini harita yolu (bej) ve binaları
   üstten gösteriyor. Buradan yol iskeleti ve "yola bitişik binalar" (olası düşme noktaları) çıkarılır.
   Favori üsler sabit olduğundan rota üs başına bir kez çıkarılıp saklanabilir.
2. **Maç içinde yol takibi:** kamera kralı ortalıyor. Kralın çevresinde yol rengi bölütlenir, rotadaki bir sonraki
   noktaya doğru yoldaki bir noktaya dokunulur ya da basılı tutulur. Kral takılırsa (kare farkı ~0) alternatif nokta denenir.
3. **Hazine tespiti:** önce tür başına şablon + renk filtresi (nesneler hareketsiz ve kamera yakınlığı sabit,
   bu çoğu durumda yeter); yetmezse küçük bir YOLO-nano modeli (CPU'da ~10-30 ms). Veri, normal koşularda
   maç içi kayıtlardan gelir.
4. **Seçici politika:** kullanıcının seçtiği tür + rotadan sapma mesafesi + kalan süre → sap, üstünden geç, rotaya dön.
   Seçilmeyen türler için sapma yapılmaz (yol üstündeyse zaten toplanır).
5. **Akış farkı:** HOME → favoriler → üs seç → saldır. Birlik/büyü çağrıları tuşla. Maç sonu aynı.
6. **Değerlendirme:** aç/kapa A/B; ölçüt saat başı elmas (ve seçilen diğer türler).

### 5.3 Açık sorular
1. Favori üsler listesi oyunda nerede açılıyor? (Leaderboard sekmelerinde ve "Prepare for Battle"da yok.)
2. Hazine maçında birlikler de kullanılacak mı, yoksa kral tek başına mı yürüyecek?
3. Düşen hazine türleri neler (elmas dışında)? Bir hazine maçını ekran kaydıyla izlersem kataloğu ben çıkarırım.
