# Proje Devir Belgesi — RR2 Farm Bot

Gerektiğinde okunur; her oturumda yüklenen özet `CLAUDE.md`'dedir. Bir alana dokunmadan önce ilgili bölümü oku.
Uzun vadeli plan ve keşif bulguları: `YOL_HARITASI.md`.

## 1. Mimari

```
supervisor.py ── başlatır/izler ──► src/bot.py (RR2Bot)
                 logs/heartbeat.txt     │
                                        ├─ controller.py  ADB: screencap, tap, swipe, keyevent, restart_game,
                                        │                 focused_package, package_version (hepsi timeout'lu)
                                        ├─ vision.py      şablon eşleme (DEFAULT_REGIONS), OCR (Tesseract)
                                        └─ player_db.py   rakip isimleri: aktif (15 dk) / kalıcı saldırılamaz
```

Ana döngü (`RR2Bot.loop`), her turda:
1. `_heartbeat()` (2 sn'de bir dosyaya yazar)
2. `current_screen()` (deneme başına 8 sn zaman aşımı); `None` ise `_handle_no_screen()`: son geçerli kareden beri
   90 sn → ADB yeniden bağlan + oyunu yeniden başlat, 5 dk → LDPlayer'ı yeniden başlat (koşulsuz)
3. `_check_interrupts()`: Break Time, forced update, "Downloading", I am the KING, Not enough Food, Shop (each ~5-6 ms, region-limited). IN_GAME'de yalnızca 3 sn'de bir.
4. Durum işleyicisi: HOME → TROPHY_MENU → FILTERED_RANKS → ATTACK_PREP → GAME_LOAD → IN_GAME → CHAMBER_OF_FORTUNE
5. 10 ardışık kaçırmada `_find_state()` (tam ekran tarama) ile yeniden senkron

## 2. Şablon arama bölgeleri

- `vision.DEFAULT_REGIONS`: şablon adı → (x1, y1, x2, y2). `find_template(..., region=None)` varsayılan bölgeyi
  kullanır; `region=FULL` tam ekran. Bölgesi olmayan şablon tam ekranda aranır (~330 ms).
- Bölgeler 2026-11-21'de oyun 12.9.0 / LDPlayer'da gözlenen konumlar + ~60 px pay. 2026-11-22 canlı doğrulandı:
  btn_melt, chest_4..6, btn_give_up ((426,491); ilk tahmin yanlıştı ve COF döngüsünü kilitliyordu).
  `btn_sell` canlıda hiç görülmedi → bölgesi yok, tam ekran aranır. take_me_back, chest_1..3 doğrulanmadı.
- `_find_state` bilerek `FULL` kullanır: buton bölgesinden kaymışsa bile yedek tarama bulabilsin.
- Oyun güncellemesinden sonra: `uv run python tools/check_regions.py fail_debug <yeni ekranlar>`.
  "!!" satırı = şablon tam ekranda bulunuyor ama bölgede bulunmuyor → bölgeyi düzelt.

## 3. Açılır pencereler ve kesintiler

| Ekran | Şablon | Davranış |
|---|---|---|
| Break Time | `popup_break_time` | Length varies (14-16 min). Wait 12 min (`_sleep`, heartbeat alive), then "Try again!" (810,505) every 30 s until the popup is gone (max 40 min). Pressing early is harmless (user). |
| Zorunlu güncelleme | `popup_update` | "Download now!" → Play Store'da `btn_play_update` → sürüm değişene kadar (≤10 dk) bekle → Play Store'u kapat → oyunu aç |
| Asset indirme | `text_downloading` | 5 sn bekle, HOME kaçırma sayaçlarını sıfırla |
| Shop | `title_shop` | X (1520,88). Durum HOME'a çekilir (IN_GAME hariç) |
| Kahraman öldü | `btn_retreat` | IN_GAME'de RETREAT. Asla RESURRECT (36 elmas) |
| Açılış teklifleri | `btn_close` | HOME kaçırmalarında X |
| Lig sonucu | `btn_collect_league` | HOME'da her kaçırmada |
| Not enough Food? | `title_not_enough_food` | Farm COLLECT (`btn_big_collect`) → else (WATCH VIDEO variant, farms empty) Shop food purchase with tokens (`btn_video` → `SHOP_COORDS` → `btn_food`, at most every 30 min; **unverified live**) → else X and wait 20 min. Then `_find_state` resync |
| I am the KING! | `btn_king_claim` | Shown after ~2 min of idling (not when another device logs in — user, 2026-11-23). Interrupt: claim at once |
| Oyun ön planda değil | `focused_package()` | HOME'da 10 kaçırmada bir; Play Store ise güncelleme akışı, değilse oyunu yeniden başlat |

## 4. Kayıtlar

- `logs/bot_*.log`: konsolun zaman damgalı kopyası (`_TeeLog`), son 30 dosya tutulur.
- `logs/events.csv`: `time, kind, state, match, detail`. Türler: `bot_start, game_restart, ldplayer_restart,
  break_time, forced_update, update_installed, update_failed, shop_closed, hero_died, play_store_in_front,
  king_claim, startup_failed, opponent_unattackable, food_collected, food_shop, food_shop_failed, food_empty`. Her `_restart_game(reason)` bir satır yazar.
- `logs/matches.csv`: `time, match, loop_s, outcome, gold, pearls, trophy_filter`. `outcome`: `finished` /
  `hero_died` / `unknown`.
- `session_log.txt`: her koşuda sıfırlanan saatlik özet (maç, ortalama döngü, inci, screencap ms, olay sayısı).
- `tools/report.py --hours N` hepsini özetler.

## 5. Tuzaklar (geçmişten öğrenilenler)

- **`adb kill-server` kullanma.** LDPlayer açılırken ADB sunucusunu sıfırlamak çalışacak bağlantıyı bozuyordu
  (bkz. commit af3ff2d). Yalnızca `adb connect` yoklaması.
- **Kör emülatör (2026-11-22):** LDPlayer yeniden başladıktan hemen sonra ADB bağlı görünürken her `screencap`
  zaman aşımına düştü; Google Play Games de "bağlanamıyor" hatası verdi. LDPlayer'ı yeniden başlatmak çözdü
  (oyunu silmek gerekmedi). Bu yüzden: `wait_boot_completed()`, zamana dayalı kurtarma ve supervisor'da
  `FRAME_TIMEOUT` (heartbeat satırı `<şimdi> <durum> <son geçerli kare>`).
- **Bot her başlangıçta LDPlayer'ı yeniden başlatır** (takılı kalmış örnek sağlıklı görünebiliyor). Supervisor
  botu yeniden başlatınca LDPlayer da yeniden başlar.
- **`ldconsole quit` zaman aşımı** bir kez eklenip geri alındı (f0770e1 → 3545cae, sebep belirtilmemiş). Takılırsa
  supervisor heartbeat ile yakalar.
- **Sabit koordinata kör basma.** Attack-prep'te (896,785) "New Enemy"dir (filtresiz rakip, 4.500 altın).
  Shop'ta Worker butonu `(140–380, 730–795)` elmas harcar. Maç içinde (1300,110) ve (1490,110), 9/0 tuşları
  15'er elmas. Ölüm ekranında RESURRECT 36 elmas.
- **Sarı butonlar birbirine benzer:** maç sonu SKIP/CONTINUE `btn_collect_league` ile ~0.84 skor verir. Eşikleri
  düşürme; bölge ve ekran bağlamıyla ayır.
- **OCR'a geçiş denendi, kırılgan çıktı** (`wip/ocr-regions` dalı): `"UP" in text`, `"BACK" in text` gibi gevşek
  eşleşmeler. Şablonlar 12.9.0'da 0.93–1.00 tutuyor; tespit için şablon tercih et.
- **Git Bash'te `adb` yolları:** `MSYS_NO_PATHCONV=1` olmadan `/sdcard/...` bozulur.
- **ADB maliyetleri:** `screencap -p` ~1.2 sn, `input tap` ~234 ms. Hızlandırma Faz 2 (scrcpy).
- **Supervisor heartbeat okuması** bot dosyayı değiştirirken anlık başarısız olabilir; tek hata öldürme sebebi değil.
- `_connect()` 127.0.0.1:5555 bağlanmadan `emulator-5554`'e düşebilir; aynı LDPlayer örneği, sorun değil.

- **Food prediction not worth it:** the food popup check costs ~6 ms per loop; OCR of the HOME food counter costs
  ~208 ms. Predicting from the counter would cost more than reacting to the popup, which spends nothing.

## 6. Açık işler

- Faz 0 çıktısı: 24 saatlik gözetimli koşu + `tools/report.py` raporu.
- `btn_archer_boosted` template not captured yet.
- Food Shop purchase (`_buy_food_in_shop`, legacy flow) not verified on 12.9.0: where `SHOP_COORDS` (1540,508)
  lands and whether `btn_food` still matches. On the first `food_shop`/`food_shop_failed` event, check fail_debug.
- Food cost depends on gear set 3 (farming set, 36 food/match on 2026-11-23); the bot taps set 3 before ATTACK.
- `btn_sell` konumu: ilk görüldüğünde (fail_debug) bölge ekle.
- Maç sonu altın OCR'ı tutarsız olabilir (ardışık maçlarda 1.09M → 0.96M → 2.31M); incele.
- Faz 1-4: `YOL_HARITASI.md`.
