# Günlük Strateji İncelemesi — 2026-09-19 (94. tur)

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Günlük görev talimatı, hedefe ulaşmak
için gereken kararları alma ve uygulama yetkisi veriyor.

## Durum
Oturum başında `origin/main` = `4bc75dd` (#166, 93. konsolidasyon turu
sonrası). Açık/bekleyen PR yoktu, dolayısıyla bu tur da gerçek, bağımsız bir
yeni hata taraması oldu.

## Bu turda yapılanlar
Ayrı bir inceleme ajanı ile 80.-93. turların "Sıradaki tur için notlar"
zincirleri okunup zaten kapsanmış dosyalar/onaylanmış ölü kod listesi
çıkarıldı; `agents/whale_tracker.py:48`'deki `market` query param sorusu
(bu oturumda da `data-api.polymarket.com`'a `curl` ile erişim `connect_rejected`
verdi — kalıcı, ağ-erişimi engelli açık madde, tekrar araştırılmadı) ve
`incident_bundle/`, `incident_bundle_v2/`, `review_bundle/`,
`architect_chamber/` dizinleri (canlı yoldan `main.py`/`docs/architecture.md`
tarafından hiç referans edilmiyor — `architect_chamber` yalnızca
`core/web_server.py`'de bir dashboard HTML route'u olarak var, trading path
değil) kapsam dışı bırakıldı. `agents/orchestrator.py::_apply_consensus_filter()`
adayı incelendi ve 87b. turda zaten "hiçbir yerden çağrılmıyor" olarak
doğrulanmış ölü kod olduğu teyit edildi — yeni bulgu değil.

### Bulunan ve düzeltilen hata: sim/paper modda `open_count`/`directional_count` cycle'lar arası devreden sim pozisyonları saymıyordu

86. günlük review, `_cycle()`'ın approved_signals döngüsündeki sim/paper
dalının (`else:` — canlı emrin `if self._is_live_trading():` karşılığı),
gerçek emir dalının yaptığı `open_count += 1` / `directional_count += 1`
bookkeeping'ini yapmadığını bulup düzeltmişti — ama bu yalnızca **aynı
cycle içindeki** sonraki sinyaller için geçerliydi.

`open_count` (satır 649) ve `directional_count` (satır 786), her
`_cycle()` çağrısının başında sırasıyla
`position_manager.open_position_count()` / `pool_position_count("directional")`
ile **sıfırdan** hesaplanıyor — bunlar yalnızca
`position_manager.data["positions"]`'ı (gerçek/canlı pozisyonlar) sayıyor
(`core/position_manager.py:166-174`). Sim/paper modda (CLAUDE.md'nin de
belirttiği fiili varsayılan çalışma biçimi) sim pozisyonları ayrı bir liste
olan `self._sim_trades`'te tutuluyor ve `_check_sim_resolutions()`
(satır 1965) bir market gerçekten resolve olana kadar (kendi docstring'i:
5-45 dakika, 60-120sn cycle aralığında ~20-45 cycle) orada kalıyor.
`self._sim_trades`'in uzunluğu `open_count`/`directional_count`
hesaplanırken hiçbir yerde okunmuyordu.

Sonuç: bir önceki cycle'larda sim-girilmiş ama henüz resolve olmamış
pozisyonlar, yeni bir `_cycle()` çağrısının başında tamamen görünmez
oluyor — `max_open_positions` (CLAUDE.md: "Aynı anda max 5 açık pozisyon",
değiştirilemez kural) ve `MAX_DIRECTIONAL = 2` kontrolleri her cycle'da
sıfırdan sayıma başlıyordu. Bot, `self._sim_target` (varsayılan sim hedefi)
kadar eş zamanlı, çözülmemiş sim pozisyonu biriktirebiliyordu — CLAUDE.md'nin
sabit 5 pozisyon / kodun kendi 2 yönlü pozisyon tavanının kat kat üzerinde.
Bu, sim/paper sonuçlarının (CLAUDE.md'deki "Sim Sonuçları" tablosunun ve her
canlıya geçiş kararının temeli) gerçekte canlı modun asla izin
vermeyeceği kadar yüksek eşzamanlı risk maruziyetiyle üretilmiş olabileceği
anlamına geliyor — sonuçlar gerçekte olduğundan daha güvenli görünüyordu.

**Düzeltme (`agents/orchestrator.py::_cycle()`):**
- `open_count` hesaplandıktan hemen sonra: `if not self._is_live_trading():
  open_count += len(self._sim_trades)`.
- `directional_count` hesaplandıktan hemen sonra: aynı desen,
  `directional_count += len(self._sim_trades)` (`self._sim_trades`'teki
  tüm girişler her zaman "directional" havuzuna ait — bond pozisyonları
  ayrı bir yoldan, `_run_bond_cycle()` → `position_manager.add_position(
  strategy="bond")` ile gerçek pozisyon olarak açılıyor, bu sim_entry
  döngüsünden hiç geçmiyor).
- `_check_sim_resolutions()` her cycle'da bu sayımlardan **önce**
  (satır 543) çalıştığı için `self._sim_trades` bu noktada zaten o
  cycle'da hâlâ gerçekten açık olan sim pozisyonları yansıtıyor —
  resolve olmuşlar `still_open` listesine (yani yeni `self._sim_trades`'e)
  dahil edilmiyor.
- 86. review'ın intra-cycle `+= 1` fix'i değişmeden korundu — iki fix
  toplamsal: cross-cycle seed bir kez, sonra döngü içinde her yeni sim
  girişinde `+= 1` devam ediyor.

**Testler** (`tests/test_sim_cross_cycle_position_caps.py`, yeni dosya, 86.
review'ın `test_sim_mode_cycle_budget_counters.py`'siyle aynı source-inspection
metodolojisi):
- `test_open_count_seeded_with_carried_over_sim_trades`
- `test_directional_count_seeded_with_carried_over_sim_trades`
- `test_carry_over_logic_precedes_the_intra_cycle_increment` — cross-cycle
  seed'in intra-cycle artışından önce geldiğini, ikisinin birbirini
  ezmediğini doğrular.

**Doğrulama:**
- Sadece `agents/orchestrator.py` geri alınıp yeni 3 test eski koda karşı
  çalıştırıldı: 3/3 fail (beklenen — testlerin gerçek hatayı yakaladığı
  doğrulandı).
- `pytest tests/test_sim_cross_cycle_position_caps.py
  tests/test_sim_mode_cycle_budget_counters.py -v` → 8/8 passed (yeni 3 +
  86. reviewın 5'i, hiçbiri diğerini bozmadı).
- Tam test suite: baseline (`origin/main`, kurulum sonrası) **1740 passed, 4
  skipped** → düzeltme sonrası **1743 passed, 4 skipped** (+3 yeni test,
  sıfır regresyon — beklenen aritmetikle birebir).
- `data/autonomous_state.json`'daki test yan etkisi commit öncesi geri
  alındı.

## Sıradaki tur için notlar
- 92./93. turların bıraktığı `SUSPICIOUS_UNDERROUND` maddesi hâlâ geçerli:
  yalnızca REJECT (sinyal eşleşmeyen) adaylar için ekli, `is_execute=True`
  olup execution_realism gate'ini geçen adaylar için kontrol edilmiyor.
  Düşük öncelikli, doğrulanması gereken açık madde olarak kalıyor.
- `agents/whale_tracker.py:48`'deki `market` query param sorusu hâlâ
  doğrulanamadı (ağ erişimi bu oturumda da `connect_rejected`). Kalıcı bir
  açık madde — yalnızca gerçek prod ağ erişimiyle çözülebilir.
- Bu turda yeni tespit edilen düşük öncelikli gözlem: `_sim_target`'e (sim
  hedefi) ulaşmadan önce `self._sim_trades`'te aynı anda kaç pozisyonun
  fiilen açık kalabileceğine dair ayrı, açık bir üst sınır yok — bu turun
  düzeltmesi mevcut `max_open_positions`/`MAX_DIRECTIONAL` tavanlarının artık
  doğru sayıldığını garanti ediyor, ama bu tavanların sim modu için
  isabetli değerler olup olmadığı (örn. sim'in kendi `_sim_target`'i ile
  tutarlılığı) ayrı bir sonraki-tur sorusu olarak bırakılıyor.
