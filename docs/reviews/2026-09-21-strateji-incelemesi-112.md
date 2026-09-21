# 112. Tur Strateji İncelemesi — 2026-09-21

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: 13:19:53 UTC. Bir önceki merge (`6eb377c`, PR #210) 12:2x UTC
civarında — aradan yine ~1 saat.

## Durum tespiti
- `git fetch origin main` → HEAD `7340a31` idi; iki açık PR bulundu (#209:
  110. tur konsolidasyonu, #210: 111. tur incelemesi), ikisi de docs-only,
  test iddiaları tutarlı, çakışma yok → ikisi de `main`'e merge edildi
  (`da6ea4e`, `6eb377c`), 104/106/110. turdaki konsolidasyon yöntemiyle
  aynı.
- Merge sonrası tam test paketi bağımsız yeniden çalıştırıldı: **1790
  passed, 4 skipped** — regresyon yok.

## Bu turda bulunan ve düzeltilen gerçek bug
İlk test çalıştırmasından sonra `git status --short` temizdi (beklenen),
ama `data/status.json` — önceki tüm turlarda "mevcut değil" olarak
kaydedilen dosya — pytest çalıştırıldıktan hemen sonra **ortaya çıktı**
(capital=100.0, initial_capital=500.0, cycle=1, open_positions=5,
positions={}, closed=[] — hiçbiri gerçek trading verisiyle tutarlı değil).

Kaynağı izole edildi (geçici bir pytest plugin ile her testten sonra
dosyanın var olup olmadığı kontrol edildi): `tests/
test_maker_cycle_daily_stop_and_lock_gate.py::
test_maker_cycle_skips_when_account_wide_position_cap_reached`,
`Orchestrator._cycle()`'ı gerçek nesneyle (`Orchestrator.__new__` +
elle bağlanmış alanlar) `open_position_count=5, max_open_positions=5,
_is_live_trading=True` ile çağırıyor ve `agents.orchestrator._sw`'yi
(diğer kardeş testlerin aksine) hiç mock'lamıyor. Bu, `_cycle()`'ın
hesap-geneli pozisyon-limiti guard'ındaki gerçek `_sw.update(...);
_sw.save()` çağrısına (agents/orchestrator.py:715-729) kadar ilerliyor ve
test fixture değerlerini doğrudan reponun gerçek `data/status.json`'ına
yazıyor.

Bu, 108. turun `data/autonomous_state.json` için bulduğu ve
`_isolate_autonomous_engine_state` fixture'ıyla düzelttiği **aynı bug
sınıfı** — 108. tur bunu yalnızca `AutonomousDecisionEngine` için
kapatmıştı, `core.status_writer`'ın kardeş sızıntısını kapsamıyordu.

**Düzeltme**: `tests/conftest.py`'ye aynı desende yeni bir autouse fixture
eklendi (`_isolate_status_writer_file`) — `core.status_writer._STATUS_FILE`'ı
her testte `tmp_path`'e yönlendiriyor. Tek merkezi düzeltme, dokuz test
dosyasına tek tek dokunmaya gerek bırakmadı (108. turdaki yaklaşımla
tutarlı).

**Doğrulama**: `data/status.json` fix öncesi kaldırıldı, tam suite tekrar
çalıştırıldı (**1790 passed, 4 skipped**, regresyon yok), test sonrası
`data/status.json` bir daha oluşmadı ve `git status --short` yalnızca
`tests/conftest.py` değişikliğini gösteriyor.

## Devam eden, kullanıcı kararı bekleyen iki bulgu (değişmedi)
1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık):
   `agents/orchestrator.py` hâlâ `_enqueue_order` (approval_queue.enqueue)
   import edip hiç çağırmıyor; sinyaller hâlâ `is_approved=True` sabitiyle
   doğrudan `client.place_order()`'a gidiyor (satır 1119/1138, 1406/1422),
   `docs/APPROVAL_WORKFLOW_SPEC.md` ise doğrudan emir yolunun kapalı
   olduğunu söylüyor. Sermaye/güvenlik etkisi nedeniyle bu tur da tek
   taraflı kod değişikliği yapılmadı — hangi tarafın doğru olduğu hâlâ
   kullanıcı kararı gerektiriyor.
2. **Zamanlama sıklığı** (106. turdan beri açık, kanıt birikmeye devam
   ediyor): Bu tur da öncekinden ~1 saat sonra tetiklendi; hesap seviyesi
   "Strateji Rutin" ayarı oturum içinden değiştirilemiyor.

## Sonuç
Bu turda gerçek bir bug bulunup düzeltildi: test suite, `data/status.json`
adlı gerçek dashboard durum dosyasına sahte veri yazıyordu. Düzeltme
sonrası kod tabanı sağlıklı (1790/1790, regresyon yok). %10 sermaye
hedefine karşı bu sandbox'ta hâlâ canlı bot örneği yok, dolayısıyla
doğrudan ölçülebilir bir kazanç/kayıp raporlanamıyor. Asıl aksiyon hâlâ
kullanıcıda: (a) onay kuyruğu/doğrudan emir çelişkisi, (b) "Strateji
Rutin" tetikleyicisinin günlük aralığa çekilmesi.
