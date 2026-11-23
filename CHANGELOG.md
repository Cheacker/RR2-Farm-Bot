# Sürüm notları

## 0.2.0 (yayımlanmadı)

Uzun, gözetimsiz çalışma için sağlamlık sürümü (Yol Haritası Faz 0).

- **Kendini kurtaran bot:** yeni `supervisor.py`, bot çökerse ya da 5 dakika boyunca yanıt vermezse onu ve
  LDPlayer'ı otomatik yeniden başlatır. Tüm ADB komutlarının artık zaman sınırı var; donmuş bir bağlantı
  botu sonsuza kadar kilitleyemez.
- **Daha hızlı ekran tanıma:** butonlar tüm ekran yerine kendi bölgelerinde aranıyor (~330 ms → ~5-10 ms).
  "ATTACK!" butonu önce şablonla, gerekirse OCR ile bulunuyor.
- **Yeni tanınan ekranlar:**
  - *Break Time* ("You've been playing for a while"): beklenip tekrar deneniyor (eskiden takılıp kalıyordu).
  - *Zorunlu oyun güncellemesi*: Play Store üzerinden otomatik güncelleniyor, oyun yeniden açılıyor.
  - *Güncelleme sonrası indirme* ("Downloading…"): beklenmeli; artık oyun yeniden başlatılmıyor.
  - *Kahraman öldü* (Resurrect/Retreat): RETREAT'e basılıyor (asla elmasla diriltmiyor); eskiden 3 dakika
    bekleyip oyunu yeniden başlatıyordu.
  - *Yanlışlıkla açılan Shop*: hemen kapatılıyor (içinde elmas harcatan butonlar var).
  - *Oyun ön planda değil* (çökme, Play Store, tarayıcı): fark edilip oyun yeniden açılıyor.
  - *JOIN LEAGUE* lig sonucu: her turda kontrol ediliyor, açılış zinciri daha hızlı geçiliyor.
- **Kayıtlar:** `logs/matches.csv` (her maç), `logs/events.csv` (her kurtarma/yeniden başlatma ve sebebi),
  `logs/bot_*.log` (zaman damgalı konsol). `tools/report.py` bunlardan özet çıkarır. Saatlik özete ekran alma
  süresi ve olay sayısı eklendi.
- **Kör kalmaya karşı:** emülatör ADB'ye bağlı görünüp ekran vermediğinde (2026-11-22'de canlı koşuda yaşandı)
  bot artık 90 sn sonra yeniden bağlanıyor, 5 dk sonra LDPlayer'ı yeniden başlatıyor. Eski "1 saat + 20 maç" şartı
  kaldırıldı; bu şart botu sonsuza kadar kör bırakabiliyordu. Supervisor da 10 dk boyunca hiç ekran gelmezse botu
  yeniden başlatıyor. LDPlayer açıldıktan sonra Android açılışının bitmesi bekleniyor.
- **Chamber of Fortune "Better luck next time":** GIVE UP butonu yanlış bölgede aranıyordu; bot 90 sn takılıp
  oyunu yeniden başlatıyordu. Düzeltildi (yanındaki 15 elmaslık TRY AGAIN'e asla basılmaz).
- **Supervisor:** heartbeat dosyası tam yazılırken okunamadığında botu gereksiz yere öldürüyordu; artık tek seferlik
  okuma hatası yok sayılıyor. `tools/report.py` Türkçe Windows konsolunda çöküyordu, düzeltildi.
- **Out of food no longer stalls the bot:** on "Not enough Food?" it now collects food from the farms. When the
  farms are empty it buys food in the Shop with tokens, and if that fails it waits 20 minutes for the farms to
  refill. It never pays with gems or watches the video. (On 2026-11-23 the bot restarted the game every 1.5 minutes
  for 5 hours on this screen.)
- **"I am the KING!"** (appears after ~2 minutes idle, e.g. after a long wait) is now claimed right away.
- **Break Time ends sooner:** instead of a fixed 15 minutes (+5 if still on break), the bot waits 12 minutes and
  then presses "Try again!" every 30 seconds, so it continues within half a minute of the break ending.
- **Hata düzeltmeleri:** LDPlayer yeniden başlatma süresinin hiç sıfırlanmaması (yazım hatası), bağlantı kopma
  mesajındaki sıfıra bölme.
- **Kurulum:** `uv` ile (`pyproject.toml`); `requirements.txt` eski yöntem için duruyor.
