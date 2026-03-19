# Incident Postmortem: INC-2026-03-15-001

## Ozet

| Alan | Deger |
|------|-------|
| Tarih | 2026-03-15 |
| Siddet | Yuksek |
| Etki | ~$10.39 kayip (SOL), 2 iptal emir (ETH), 1 iptal emir (DOGE) |
| Suresi | ~2 saat (dual-instance calisma suresi) |
| Root Cause | Cift bot instance + eksik guvenlik kapilari |

## Zaman Cizelgesi

| Saat (UTC) | Olay |
|------------|------|
| ~14:00 | Bot ilk kez baslatildi (main.py) |
| ~14:05 | Ikinci instance baslatildi (ilki kapatilmamis) |
| ~14:10 | Interleaved loglar: Dongu #4 vs #2 ayni anda |
| ~14:15 | SOL markete 2 emir (farkli instance'lardan) |
| ~14:20 | ETH markete 2 emir (LIVE on CLOB kesildi) |
| ~14:30 | DOGE markete emir (ayni market 4 kez denendi) |
| ~16:00 | Dashboard'da kayip gozukmuyor — status.json stale |
| ~19:20 | Operator mudahale, CLOB'dan emirler cancel edildi |

## Root Cause Analizi

### 1. Cift Instance (Ana Sebep)
- `main.py`'de PID-based lock vardi ama inline ve kirilgandi
- Ikinci `python main.py` calistirmasi ilk instance'i durdurmadan baslatildi
- Lock dosyasi kontrolu race condition'a acikti

### 2. Reentry Guard Yoklugu
- Bot ayni market'e (DOGE) 4 kez emir verdi
- Cooldown mekanizmasi yoktu — kapanan pozisyon ayni dongude tekrar aciliyordu

### 3. Expiry Guard Yoklugu
- `_hours_to_close()` None donunce filtre geciriyordu
- Expired market'lere emir verildi

### 4. Rate Limit Yoklugu
- Saatlik emir limiti yoktu
- Kisa surede cok fazla emir verildi

### 5. Dashboard Stale Data
- `status.json` bot durdugunda guncellenmiyordu
- `positions.json`'daki gercek veri dashboard'a yansimiyordu
- PnL hesabi hatali: tum pozisyonlar 0.0 gosteriyordu

## Alinan Dersler

1. **Tek instance garantisi zorunlu**: PID dosyasi + `sys.exit(1)` ile enforce
2. **Per-market cooldown sart**: Kapanan market 24 saat icerisinde tekrar acilamaz
3. **Expiry kontrolu zorunlu**: `None` = bilinmiyor = RED (fail-safe)
4. **Rate limiting**: Saatte max N emir
5. **Dashboard canli veri**: `positions.json`'dan merge edilmeli

## Uygulanan Duzeltmeler

### Faz 1: Control Plane Modulleri
| Modul | Dosya | Gorev |
|-------|-------|-------|
| ProcessLock | `control_plane/process_lock.py` | PID-based singleton |
| ApprovalQueue | `control_plane/approval_queue.py` | State machine: PENDING -> APPROVED -> EXECUTED |
| ReentryGuard | `control_plane/reentry_guard.py` | Session + persistent 24h cooldown |
| ExpiryGuard | `control_plane/expiry_guard.py` | EXPIRED/TOO_NEAR/TOO_FAR/NO_END_DATE red |

### Faz 2: Live Gate
| Dosya | Gorev |
|-------|-------|
| `control_plane/live_gate.py` | 10-nokta guvenlik kontrolu |

10 Kontrol Noktasi:
1. process_lock — Tek instance
2. live_trading — control.json bayragi
3. readiness — Verdict dosyasi taze & gecerli
4. daily_stop — Gunluk kayip limiti
5. position_count — Max acik pozisyon
6. rate_limit — Saatlik emir limiti
7. reentry_guard — Market cooldown
8. expiry_guard — Market suresi
9. approval — Emir onayi
10. capital — Yeterli sermaye

### Faz 3: Entegrasyon
- Orchestrator: Inline guvenlik kodu -> control_plane modulleri
- Web Server: `/api/gate` endpoint — canli gate durumu
- Dashboard: Live Gate paneli — 10 kontrol gorsellestirmesi

## Onleme Stratejisi

| Risk | Onlem | Durum |
|------|-------|-------|
| Cift instance | ProcessLock + sys.exit(1) | TAMAMLANDI |
| Ayni markete tekrar giris | ReentryGuard (24h cooldown) | TAMAMLANDI |
| Expired market emri | ExpiryGuard (4 rejection reason) | TAMAMLANDI |
| Onaysiz emir | ApprovalQueue state machine | TAMAMLANDI |
| Toplu guvenlik kontrolu | LiveGate 10-nokta | TAMAMLANDI |
| Dashboard stale veri | positions.json merge | TAMAMLANDI |

## Sonuc

Bu incident, trading botunda **safety-critical** eksiklikleri ortaya cikaradi.
Tum duzeltmeler `control_plane/` paketi altinda merkezi, test edilebilir moduller olarak uygulanmistir.
Her canli emir vermeden once 10-nokta LiveGate kontrolunun gecmesi zorunludur.
Tek bir kontrol bile basarisiz olursa emir EXECUTION_BLOCKED durumuna alinir.
