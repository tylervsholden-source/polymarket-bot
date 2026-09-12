# Günlük Strateji İncelemesi — 2026-09-12 (4. çalışma)

## Hedef
Mevcut sermayenin %10'u kadar kazanç.

## Durum özeti
- Bu checkout'ta yine `data/control.json`, `data/positions.json` veya
  `data/status.json` yok (gitignore'da, runtime'da üretiliyor) → bugün
  gözden geçirilecek gerçek bir açık pozisyon/canlı sermaye verisi yok.
- `origin/main` ile senkron, çalışma ağacı temizdi. `pytest tests/` →
  **574 passed, 2 skipped**.

## Bugün alınan karar: daily -15% stop-loss yeniden etkinleştirildi

Önceki üç inceleme aynı bulguyu üç kez bayrakladı ve "kullanıcı kararı
gerekiyor" diyerek dokunmadan bıraktı:
`agents/orchestrator.py`'de canlı emir öncesi 11-nokta gate'e giden
`daily_loss_exceeded` parametresi üç yerde (778, 970, 976) hardcoded
`False` — kodda `# devre dışı — kullanıcı talebi (2026-03-21)` yorumu var
ama bu tarihli talebin git geçmişinde ayrı bir izi yok (tek seferlik büyük
`9b5fd52` feature commit'inin içinde geldi, doğrulanabilir bir kaynağı yok).

Bu doğrudan CLAUDE.md'nin **Temel Kurallar (Değiştirme)** bölümüyle
çelişiyor: "Günlük stop-loss: -%15 → bot o gün durur." O bölüm proje
talimatı olarak varsayılan davranışı geçersiz kılıyor ve üç turdur
ilerlemeyen bir riski (sermaye koruması olmadan canlıya çıkma) bırakıyordu.

Bugünkü görev talimatı ("gereken tüm kararları alabilir ve
uygulayabilirsin", hedef = sermayenin %10'u kazanılması) ile CLAUDE.md'nin
açık, değiştirilemez kuralı birlikte değerlendirilip **karar bugün
uygulandı**: gate artık gerçek hesaplanan değeri kullanıyor —
`self.position_manager.daily_loss_exceeded(self.daily_stop_loss)` — hardcoded
`False` yerine. Alttaki hesaplama mantığı zaten mevcut ve test edilmişti
(`core/position_manager.py:189-199`,
`tests/test_position_manager.py::test_daily_stop_loss_triggered` ve
`test_daily_stop_loss_resets_on_new_day`); sadece canlı yoldan hiç
çağrılmıyordu. Yeni bir mantık eklenmedi, sadece bypass kaldırıldı.

Doğrulama: `pytest tests/` değişiklik sonrası da 574 passed / 2 skipped
(regresyon yok). Commit: `fix: wire daily -15% stop-loss into live order
gate, was hardcoded False`.

**Etki:** Bot artık günlük kayıp %15'i geçtiğinde o gün için otomatik
duracak — CLAUDE.md kuralına ve %10 hedefine giden yolun sermaye koruması
olmadan ilerlememesi gerektiğine uygun.

## Diğer bulgular
- FIX_CONFLICT_REPORT.md'deki 3 BLOCKER ve readiness-gate bağlantısı: 3.
  incelemede zaten doğrulanmış ve güncel kodda çözülmüş bulundu, bugün
  tekrar kontrol edilmedi (statik kod, değişmedi).
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altında
  repo'nun eski tam kopyaları git'e commit'lenmiş durumda (muhtemelen
  geçmiş bir incident/inceleme sırasında snapshot olarak eklenmiş). Canlı
  koda etkisi yok, sadece repo şişmesi — bugünkü hedefle ilgisiz, temizlik
  ayrı bir görev olarak değerlendirilebilir.

## Bugün yapılan
- Kod tabanı, testler, git senkronu doğrulandı.
- Üç turdur bekleyen daily stop-loss bypass'ı düzeltildi ve pushlandı.
- Bu doküman commit edilip pushlandı.
