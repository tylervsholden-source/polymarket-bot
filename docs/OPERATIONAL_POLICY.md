# Operational Policy

## Amac
Bot isletim kurallari ve guvenlik politikasi.
INC-2026-03-15-001 sonrasi olusturulmustur.

## Canli Islem Onkosuallari

Bot canli emir verebilmesi icin TUMU saglanmalidir:

1. **Tek instance**: `data/bot.lock` dosyasi bu process'e ait olmali
2. **Live trading bayragi**: `data/control.json` -> `live_trading: true`
3. **Readiness verdict**: `data/readiness_verdict.json` -> `TINY_PILOT_CANDIDATE`, max 26 saat eski
4. **Gunluk stop-loss**: Gunluk kayip < %15
5. **Pozisyon limiti**: Acik pozisyon < 5 (MAX_OPEN_POSITIONS)
6. **Saatlik emir limiti**: Son 1 saat < 3 emir (MAX_ORDERS_PER_HOUR)
7. **Market cooldown**: Hedef market son 24 saatte islem gormemis
8. **Market suresi**: Expired/too near/too far degil
9. **Operator onayi**: Dashboard'dan APPROVED
10. **Yeterli sermaye**: available_capital >= order_amount

## Emir Akisi

```
Sinyal uretildi
  -> ApprovalQueue.enqueue() [PENDING]
  -> Dashboard'da gosterilir
  -> Operator onaylar [APPROVED]
  -> Sonraki dongude LiveGate 10-nokta kontrol
  -> PASS: CLOB API'ye emir verilir [EXECUTED]
  -> FAIL: emir engellenir [EXECUTION_BLOCKED]
```

## Risk Limitleri

| Parametre | Varsayilan | Env Variable |
|-----------|------------|-------------|
| Max tek pozisyon | portfoyun %20'si | Kelly cap |
| Gunluk stop-loss | -%15 | DAILY_STOP_LOSS_PCT |
| Max acik pozisyon | 5 | MAX_OPEN_POSITIONS |
| Min market hacmi | $5,000 | MIN_VOLUME |
| Min edge | 0.04 | MIN_EDGE_THRESHOLD |
| Saatlik emir limiti | 3 | MAX_ORDERS_PER_HOUR |
| Market cooldown | 24 saat | ReentryGuard |
| Readiness max yas | 26 saat | READINESS_MAX_AGE_HOURS |

## Izleme

### Dashboard (http://localhost:8080)
- Sol panel: Portfoy durumu, kontroller
- Orta panel: Islem gecmisi, bekleyen emirler
- Sag panel: **Live Gate paneli (10 kontrol)**, acik pozisyonlar, sinyaller

### API Endpointleri
| Endpoint | Metod | Aciklama |
|----------|-------|----------|
| /api/status | GET | Bot durumu |
| /api/control | GET/POST | Kontrol bayraklari |
| /api/gate | GET | 10-nokta LiveGate durumu |
| /api/pending | GET | Emir kuyrugu |
| /api/pending/approve | POST | Emir onayla |
| /api/pending/reject | POST | Emir reddet |

## Incident Proseduru

1. Bot'u durdur: `kill <PID>` veya `data/bot.lock` sil
2. `data/control.json` -> `live_trading: false`
3. CLOB API'den acik emirleri kontrol et
4. Gerekirse `py_clob_client` ile cancel et
5. Root cause analizi yap
6. Duzeltmeleri uygula ve test et
7. Postmortem yaz (`docs/INCIDENT_POSTMORTEM_*.md`)

## Degisiklik Yonetimi

- **Strateji degisikligi**: YASAK (bu sprint sadece guvenlik)
- **Yeni agent ekleme**: YASAK (bu sprint sadece guvenlik)
- **Guvenlik modulu**: control_plane/ altina eklenir
- **Test**: Her modul icin ayri test dosyasi zorunlu
