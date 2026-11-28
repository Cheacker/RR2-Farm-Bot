# Kurulum ve çalıştırma

Python botu; derleme adımı yok. Ön koşullar: LDPlayer 9 (1600x900), `adb` ve `ldconsole.exe` PATH'te,
Tesseract `C:\Program Files\Tesseract-OCR\`, [uv](https://docs.astral.sh/uv/). Ayrıntı: `README.md`.

## Gözetimli çalıştırma (24 saat+ için önerilen)

```powershell
cd rr2-farm-bot-public
uv sync
uv run python supervisor.py --trophy-filter 600 --gold 1000000
```

`supervisor.py` sonrasındaki tüm argümanlar `src/bot.py`'ye aynen geçer. Bot çökerse ya da 5 dakika
heartbeat yazmazsa supervisor onu (ve LDPlayer'ı) yeniden başlatır. Durdurmak için: Ctrl+C.

## Tek seferlik çalıştırma (gözetimsiz değil)

```powershell
uv run python src/bot.py --trophy-filter 600 --gold 1000000
```

## Koşu sonrası rapor ve kontroller

```powershell
uv run python tools/report.py --hours 24                         # maç/saat, inci/saat, olaylar, yeniden başlatmalar
uv run python tools/check_regions.py fail_debug <ekran-klasörü>  # şablon bölgeleri oyun güncellemesinden sonra hâlâ doğru mu
```

## Hazine verisi (treasure modu kareleri)

```powershell
uv run python tools/treasure_label.py      # karelerde hazineleri tıklayarak işaretle (tuşlar: dosyanın başında)
uv run python tools/treasure_dataset.py    # işaretlerden kırpıntı veri seti: dataset/treasure/
```

Kayıtlar `logs/` altında: `bot_*.log` (konsol), `matches.csv`, `events.csv`, `supervisor.log`, `heartbeat.txt`.
