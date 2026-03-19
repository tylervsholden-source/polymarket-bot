# INC-2026-03-15-001 — Forensic Incident Bundle v2

## Olay Özeti
- **Tarih:** 2026-03-15, 22:23-22:31 UTC+3
- **7 yetkisiz emir:** $72.73 nominal değer
- **Gerçek kayıp:** $10.39 (sadece 1 emir eşleşti)
- **CLOB bakiye:** $148.44 → $138.056

## Bundle İçeriği

### incident/ — Olay Belgeleri
- `timeline.txt` — Dakika dakika olay akışı
- `order_event.json` — 8 emrin CLOB API doğrulaması
- `root_cause_analysis.txt` — 5 köksel neden detayı
- `runtime_env_sanitized.txt` — Olay anı environment
- `run_command.txt` — Bot nasıl çalıştırıldı

### data/ — State Dosyaları
- `control.json` — Live trading flag (olay sonrası: false)
- `status.json` — Dashboard state (STALE — bu bir sorun)
- `positions.json` — Pozisyon ve PnL kaydı (düzeltilmiş)
- `readiness_verdict.json` — Readiness gate durumu
- `shadow_journal_2026-03-15.jsonl` — Tüm karar geçmişi
- `positions.json.bak*` — Olay öncesi yedekler

### logs/ — Bot Logları
- `bot.log` — ANA LOG: 22:23-22:31 arası 7 emir kanıtı
- Ek loglar: önceki günler

### Kod Dosyaları
- `main.py` — Giriş noktası (PID lock eklendi)
- `core/` — polymarket_client, position_manager, web_server, approval_queue
- `agents/` — orchestrator (ana döngü), binance_feed, smart_trader
- `strategies/` — arbitrage_engine, kelly_criterion
- `operator_layer/` — Tüm operator modülleri
- `architect_chamber/` — Dashboard HTML
- `signal_bridge/` — Sinyal yönlendirme
- `calibration/` — Kalibrasyon
- `execution_realism/` — Gerçekçilik modeli
- `shadow_runner/` — Shadow test motoru
- `web/` — Dashboard frontend (approval UI eklendi)

## Önemli Dosyalar (Öncelik Sırasıyla)
1. `incident/root_cause_analysis.txt` — 5 köksel neden
2. `logs/bot.log` — 380-682 satırları: olay kanıtı
3. `data/control.json` — Live trading flag
4. `data/readiness_verdict.json` — Gate bypass kanıtı
5. `incident/order_event.json` — CLOB doğrulaması
6. `agents/orchestrator.py` — Emir veren kod yolu
7. `core/polymarket_client.py` — place_order() fonksiyonu
