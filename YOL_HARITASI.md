# RR2 Farm Bot — İnceleme ve Yol Haritası (2026-11-21)

Hedef: kısa farm döngüsü + **24 saatten uzun, gözetimsiz** çalışma + kullanıcının her ekranı elle
test etmek zorunda kalmaması. Sonraki aşama: maç içinde düşen hazineleri seçerek toplamak.

---

## 1. Mevcut durumun teşhisi

### 1.1 Ölçülen maliyetler (bu makinede, 1600x900 kare)

| İşlem | Süre |
|---|---|
| Tesseract OCR, tek bölge (`read_region_text`) | **~295 ms** (her çağrı ayrı `tesseract.exe` süreci açar) |
| Tam ekran şablon eşleme (`find_template`, bölgesiz) | **~333 ms** |
| Bölgeli şablon eşleme (`region=` verilmiş) | **~6 ms** |
| PNG kodlama (PC tarafında) | ~90 ms; `screencap -p` cihazda aynı işi yapar ve üstüne aktarım ekler |

Sonuç: işlemciyi yiyen şey ekran görüntüsünün kendisinden çok **durum tespiti için OCR ve bölgesiz
şablon aramaları**. Örnekler:
- `GAME_LOAD` her turda: `btn_video` tam ekran (~333 ms) + `_has_take_me_back` OCR (~295 ms, bazen 2 kez).
- `_find_state` tek çağrıda 10+ OCR ve birkaç tam ekran arama yapabilir → birkaç saniye.
- `IN_GAME` her 45 sn'de `_find_state` çalıştırıyor; maç içinde dokunma ritmini bozuyor.

### 1.2 Mimari sorun: "varsayılan durum" + kör dokunuş

Durum makinesi `self.state` ile **ekranın ne olduğunu varsayıyor**, ekran ona uymayınca kaçırma sayaçları
(`_anchor_miss_streak`, `_no_opponent_count`, `_trophy_menu_miss`…) dolana kadar o ekranın sabit
koordinatlarına basmaya devam ediyor. Commit geçmişinin büyük kısmı bu sınıfın yamaları:
`_leave_attack_prep`, `_scroll_list` içindeki "New Opponent" koruması, RESYNC, `GOING_BACK`…
Her yeni açılır pencere yeni bir yama ve yeni bir manuel test turu demek.

### 1.3 `fail_debug/` ne söylüyor (22 kare)

- **~8 kare "Break Time" ekranı**: *"You've been playing for a while, time for a break – please try again later"*.
  Oyunun kendisi uzun oturumları kesiyor. 24 saat çalışmanın **bir numaralı engeli** bu; bekleyip
  yeniden denemekten başka yolu yok, süresi ölçülmeli.
- **~6 kare Shop (Misc sekmesi)**: bot dükkânda kalmış, `TROPHY_MENU` 300 tur boyunca `BLUE_SEARCH_COORDS`'a
  kör basıyor. Dükkânda kör dokunuş **elmas harcatabilir** (Worker satın alma butonu `(140–380, 730–795)`,
  `GAME_LOAD`'un her turda bastığı `(240, 800)`'e çok yakın). İki kare arasında elmas sayısında büyük
  düşüş görünüyor; kullanıcının bilerek harcayıp harcamadığı kontrol edilmeli.
- **Kahraman öldü ekranı** (RESURRECT 36 elmas / USE TOKEN / RETREAT): hiç işlenmiyor → 3 dakikalık
  zaman aşımı + oyunu yeniden başlatma. RETREAT'e basmak yeterli olurdu.
- "Festival Earnings → Gems COLLECT" gibi tek seferlik etkinlik pencereleri.

### 1.4 Somut hatalar / riskler

| # | Yer | Sorun |
|---|---|---|
| H1 | `bot.py:436,444,462,876` | `btn_take_a_break`, `btn_king_claim`, `btn_archer_boosted` şablonları `En_Templates/`'te **yok** → bu kontroller hiç çalışmıyor (sessizce `None`). |
| H2 | `bot.py:240` | `self.last_restart_start_time` (alt çizgisiz) yazılıyor, okunan `self._last_restart_start_time` → LDPlayer yeniden başlatma kapısının süre sayacı hiç sıfırlanmıyor. |
| H3 | `bot.py:267,290` | `self._last_restart_match_count/self._match_count` → maç sayısı 0'ken `ZeroDivisionError`; genel `except` yutuyor. |
| H4 | `controller.py` `tap/swipe/keyevent` | ADB `shell` çağrılarında `timeout` yok; soket donarsa bot sonsuza kadar asılı kalır. Genel `except` bunu yakalayamaz (hata değil, bekleme). |
| H5 | `bot.py:241`, `subprocess.run(... quit)` | `ldconsole` çağrılarında timeout yok. |
| H6 | `player_db.py` | 3.9 MB JSON her `mark_active`/süre dolumunda **tamamen** yeniden yazılıyor, atomik değil (yarıda öldürülürse dosya bozulur), hiç budanmıyor. |
| H7 | genel | Loglar yalnızca konsolda; 24 saatlik bir koşudan sonra "ne oldu" sorusunun cevabı yok (`session_log.txt` sadece saatlik özet). |
| H8 | `RESTART_GAME_WAIT = 18` vb. | Sabit beklemeler; ekran hazır olunca devam etmek yerine en kötü durum kadar bekleniyor. |
| H9 | depo | Çalışma ağacında commitlenmemiş büyük değişiklik var (`bot.py` +~290 satır); proje `CLAUDE.md`'si MEmu dönemine ait ve gitignore'da. |

---

## 2. Sorulara cevaplar

### "Sabit tıklamalar mantıklı mı?"
Evet, hız için doğru tercih. Kural şu olmalı: **ekranı teyit et → sabit dokunuş dizisini körlemesine bas →
sonraki ekranı teyit et.** Ara adımlarda görüntü işleme gereksiz; ama dizinin başı ve sonu mutlaka
doğrulanmalı. Şu anki hataların hepsi "teyit edilmemiş ekrana sabit dokunuş"tan çıkıyor.

### "Ekran görüntüsü yerine process listener kullanabilir miyiz?"
Kısaca: oyunun ekran durumunu dışarıya bildiren hazır bir sinyal yok; ama ucuz yollar var.

| Yol | Ne verir | Değerlendirme |
|---|---|---|
| `dumpsys activity/window` | Hangi Activity önde | Oyun tek Activity (Unity) → ekran ayrımı yok. Sadece "oyun çöktü mü / ön planda mı" için işe yarar. |
| `adb logcat` (Unity etiketi) | Oyunun kendi logları | **10 dakikalık deneye değer.** Sahne geçişi, maç başı/sonu logluyorsa bedava ve anlık olay kaynağı olur. Logluyorsa durum tespitinin bir kısmı görüntüden kurtulur. |
| Ağ trafiği (mitmproxy/tcpdump) | Maç başı/sonu, ödül API çağrıları | TLS + sertifika sabitleme muhtemel; zahmetli. Önerilmez. |
| Bellek okuma / Frida / enjeksiyon | Her şey | Hile tespiti ve ban riski, ToS ihlalinin bir üst seviyesi. **Önerilmez.** |
| **scrcpy video akışı** | Sürekli kare akışı (30–60 fps), son kare her an bellekte | Kare almak ~0 ms olur; `screencap -p`'nin cihazdaki PNG maliyeti ve gecikmesi kalkar. Hazine toplama için **şart**. |
| Ham `screencap` (`-p`'siz) | Sıkıştırmasız RGBA | Kolay ara adım; cihazdaki PNG kodlamasını atlar. |
| LDPlayer penceresini Windows'tan yakalama (DXGI/PrintWindow) | Çok hızlı | Pencere görünür olmalı; DirectX/OpenGL'de siyah kare riski. Yedek seçenek. |

Asıl kazanç: **durum tespitinde OCR'ı ve bölgesiz aramayı kaldırmak.** Her ekran 2–3 küçük bölgeli
şablon veya birkaç piksel rengiyle tanınabilir → ekran sınıflandırma kare başına ~5–15 ms.
OCR yalnızca sayı (altın/inci/kupa) ve rakip ismi için kalır, o da durum belli olduktan sonra.

### "Oyunu sen oynayıp incelesen, ben her durumu review etmesem"
Yapılabilir: LDPlayer açıkken ADB üzerinden ekran alıp bakabiliyor ve dokunabiliyorum. Bunu tek seferlik
bir gezinti değil, **kalıcı bir sistem** olarak kurmayı öneriyorum (Faz 1):
1. **Ekran atlası:** her ekran için örnek kareler + imza (hangi bölgede hangi şablon/renk) tek bir veri
   dosyasında (`screens.yaml`). Ekranları ben gezip çıkarırım.
2. **Bilinmeyen ekran kutusu:** bot tanımadığı her kareyi (benzerleri tekilleştirerek) `unknown/`'a kaydeder.
   Senin işin "bot takıldı mı" diye izlemek değil; arada bir bana "unknown'a bak" demek. Ben etiketlerim,
   imza eklerim.
3. **Çevrimdışı regresyon testi:** etiketli her kare bir test olur (`tests/screens/<ekran>/*.png`).
   `uv run pytest` sınıflandırıcının hepsini doğru tanıdığını emülatörsüz doğrular. Değişiklik sonrası
   manuel test ihtiyacının büyük kısmı burada biter.

---

## 3. Hedef mimari

```
 FrameSource ──► ScreenClassifier ──► Policy (ekran → eylem) ──► Controller
 (scrcpy/raw)     (screens.yaml,        (sabit dokunuş makroları,   (ADB, hepsi
                   ~10 ms, OCR yok)      her makro sonunda teyit)    timeout'lu)
                         │
                         └─ tanınmayan → kurtarma merdiveni + unknown/'a kaydet

 Supervisor (ayrı süreç): botu başlatır, heartbeat dosyasını izler, asılırsa öldürüp yeniden başlatır,
 Break Time beklemesini yönetir, günlük rapor üretir.
```

- **Algıla → karar ver döngüsü:** her karede "bu hangi ekran?" sorusu sorulur; `self.state` artık tahmin değil,
  gözlem. RESYNC, `GOING_BACK`, miss sayaçlarının çoğu ortadan kalkar. Hız kaybı yok: sınıflandırma ~10 ms.
- **Beklenen ekran + zaman aşımı:** her makro "şu ekranlardan birine geçmeliyim, en geç N sn" bilgisini taşır.
  Sabit `sleep`'ler yerine `wait_for(ekranlar, timeout)` → döngü kısalır.
- **Kurtarma merdiveni** (bilinmeyen ekran süresi arttıkça): bilinen kapat butonları → Android BACK →
  oyunu yeniden başlat → LDPlayer'ı yeniden başlat. Hiçbir basamak elmas harcayabilecek bir bölgeye
  kör dokunmaz.
- **Kalıcı kayıt:** `matches.csv` (her maç: süre, sonuç, altın/inci farkı, rakip), `events.jsonl`
  (her ekran geçişi ve kurtarma), dönen dosya loglar. Oyuncu DB'si SQLite'a (atomik, budanabilir).
- **Supervisor:** 24 saat hedefinin sigortası. Bot içinde ne asılırsa assın (H4, H5) dışarıdan kurtarılır.

---

## 4. Fazlar

Her faz tek başına kullanılabilir bir kazanç bırakır; büyük patlama yeniden yazım yok.

### Faz 0 — Sağlamlık ve ölçüm (küçük, hemen)
- Commitlenmemiş çalışmayı netleştir (H9), `bot_backup_*.py`'ı kaldır.
- H1–H5 düzeltmeleri; RETREAT ekranı (kahraman öldü); Shop ekranı tespiti → kapat.
- Break Time: tespit et, kaç dakika sürdüğünü logla, bekle (şimdilik sabit, ölçünce akıllı).
- Tüm ADB/subprocess çağrılarına timeout; heartbeat dosyası + basit supervisor betiği.
- Dosya log + `matches.csv`; her aşamanın süresini ölç (kare alma / tespit / bekleme).
- **Çıktı:** 24 saatlik deneme koşusu ve sayılarla rapor (maç/saat, inci/saat, kurtarma sayısı, Break Time sayısı).

### Faz 1 — Ekran atlası ve sınıflandırıcı (benim işim, senin emülatörünle)
- LDPlayer açıkken ekranları gezip atlas çıkarırım (`fail_debug/` mevcut 22 kare de dahil).
- `screens.yaml` + `ScreenClassifier` + `tests/screens/` regresyon testleri.
- Bot önce **gölge modda**: eski mantık çalışır, sınıflandırıcı yanında çalışıp anlaşmazlıkları loglar.
  Anlaşmazlık sıfıra inince Faz 3'e geçilir.

### Faz 2 — Hız
- Kare kaynağı: önce ham `screencap`, sonra scrcpy akışı; ölçerek karşılaştır.
- Durum tespitinden OCR ve bölgesiz aramayı tamamen çıkar.
- Opsiyonel: `adb logcat` denemesi; oyun işe yarar log basıyorsa olay kaynağı olarak ekle.
- **Ölçüt:** ortalama döngü süresi (maç dışı kısım) Faz 0 ölçümüne göre.

### Faz 3 — Algıla/karar ver döngüsüne geçiş
- Ekran ekran eski handler'ların yerine politika tablosu. Sırayla: COF → Shop/popup'lar → HOME → TROPHY_MENU
  → FILTERED_RANKS → ATTACK_PREP → GAME_LOAD → IN_GAME.
- Her geçişte Faz 0'daki 24 saatlik koşu tekrarlanır, sayılar kıyaslanır.

### Faz 4 — Maç içi hazine toplama (ayrıntı aşağıda)

---

## 5. Hazine toplama planı

Zor kısım doğru: konum rastgele, kamera kahramanı takip ediyor, nesneler animasyonlu ve üst üste binebiliyor,
üstelik **seçici** olmak için türünü de bilmek gerekiyor. Bu yüzden önce veri, sonra model.

**Ön koşul:** Faz 2'deki akış (scrcpy). Saniyede 1 kare ile hareketli hazine kovalanmaz.

1. **Kayıt (emeksiz):** normal bot koşularında maç içi kareler 3–5 fps diske yazılır (yalnızca IN_GAME).
   Birkaç düzine maç yeterli başlangıç verisi.
2. **Katalog (ben):** kareleri inceleyip düşen nesne türlerini çıkarırım: görünüş, ne kadar ekranda kaldığı,
   toplamak için dokunmak mı gerekiyor yoksa kahramanın üstünden geçmesi mi, kaybolma süresi.
   Değer sıralamasını sen verirsin (hangisi toplanmaya değer).
3. **Tespit — iki kademe:**
   - *Hızlı başlangıç:* tür başına birkaç şablon + HSV renk maskesi ön filtresi (parıltı/renk çok ayırt ediciyse
     bu yeterli olabilir; kamera yakınlığı sabit olduğundan ölçek pek değişmez).
   - *Sağlam çözüm:* küçük bir nesne tespit modeli (YOLO "nano" sınıfı). CPU'da 640 px'de kare başına
     ~10–30 ms. Etiketleme: şablon eşleşmeleri ilk etiketleri otomatik üretir, ben düzeltirim; tür başına
     birkaç yüz örnek genelde yeter.
4. **Eylem politikası:** maç ilerleme dokunuşu ile çakışıyor, o yüzden basit bir kural:
   değerli nesne + kahramana yakın + zaman bütçesi var → nesneye dokun, kaybolana kadar (veya 2–3 sn) bekle,
   ilerlemeye dön. Zaman bütçesi ve yarıçap ayarlanabilir.
5. **Değerlendirme:** özellik aç/kapa ile A/B; ölçüt "saat başı kazanç", çünkü toplamak maçı uzatır.
   Döngü süresi artışı kazancı yiyorsa kapalı kalır.

Belirsizlikler (kayıt/katalog adımında netleşecek): toplama dokunuşla mı yürüyerek mi, nesneler ne kadar
yaşıyor, maç sonunda otomatik toplanıyorlar mı (öyleyse hiç gerek kalmayabilir).

---

## 6. Açık sorular (kullanıcıya)

1. Hazineler dokunarak mı toplanıyor, kahraman üstünden geçince mi? Hangileri değerli?
2. Break Time ne sıklıkla ve ne kadar süre çıkıyor, biliyor musun? (Bilmiyorsak Faz 0 ölçecek.)
3. Commitlenmemiş `bot.py`/`vision.py` değişiklikleri istenen hâl mi, yoksa deneme mi?
