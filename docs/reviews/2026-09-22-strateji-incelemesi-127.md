# 127. Tur Strateji İncelemesi — 2026-09-22

## Kapsam
Planlı ("her gün stratejini gözden geçir, sermayenin %10'u kadar kazanma
hedefi için gereken kararları al") görevin bu turdaki çalıştırması.
Tetiklenme: ~11:13 UTC.

## Durum tespiti
- Bu tur başladığında `main` üzerinde açık bir PR vardı: #227 ("126. tur"),
  10:07:14 UTC'de başka bir oturum tarafından oluşturulmuş,
  `mergeable_state: clean`, 0 CI check (repoda CI workflow yok, önceki
  turlarla tutarlı). İçeriği bağımsız doğrulandı (bkz. aşağı) ve merge
  edildi (`1627dc9`). Yerel `claude/brave-faraday-l4xgkw` dalı
  `origin/main`'e fast-forward edildi — dalda kayıp iş yoktu.
- Kadans: 126. tur PR'ı 10:07:14 UTC oluşturuldu, bu tur ~11:13 UTC
  başladı — fark ~66 dakika. 106. turdan beri açık olan "günlük yerine
  saatlik tetikleniyor" bulgusu bu turda da (yedinci kez art arda ~1
  saatlik aralıkla) doğrulandı.

## Bug taraması
İki bilinen bulgu kaynak koddan yeniden doğrulandı (`agents/orchestrator.py`),
ikisi de değişmemiş:

1. **Onay kuyruğu / doğrudan emir yolu çelişkisi** (104. turdan beri açık):
   `grep -c '_enqueue_order(' agents/orchestrator.py` → 0 — satır 42'de
   import ediliyor ama hiç çağrılmıyor; canlı sinyal döngüsü (~satır 1119)
   ve onay-sonrası yürütme yolu (~satır 1406) hâlâ `is_approved=True`
   sabitiyle doğrudan `client.place_order()`'a gidiyor. Bu tur bulguyu
   birincil kaynaklardan (`docs/INCIDENT_POSTMORTEM_INC_2026_03_15_001.md`,
   `docs/OPERATIONAL_POLICY.md`) yeniden doğruladım: `ApprovalQueue`,
   INC-2026-03-15-001 sonrası "Sinyal → emir arasında insan onayı ZORUNLU"
   dersiyle eklenmiş, `OPERATIONAL_POLICY.md`'deki 10 canlı-emir
   önkoşulundan biri hâlâ "Operator onayı: Dashboard'dan APPROVED". Kod
   bunu atlıyor. Ancak bu, CLAUDE.md'deki v3 mimarisinin (ReviewerAgent
   Claude API ile APPROVE/VETO/REDUCE veriyor, AutonomousDecisionEngine
   EXECUTE/SKIP/DEFER karar veriyor) insan operatörü kasıtlı olarak AI
   reviewer ile ikame edip etmediği belgelenmemiş — iki okuma da mümkün:
   (a) belge güncellenmemiş, kasıtlı mimari evrim, ya da (b) gerçek bir
   güvenlik regresyonu. Bu ayrım sadece kullanıcı kararıyla çözülür; kod
   değişikliği (kuyruğu bağlamak canlı işlemi durdurur, bağlamamak mevcut
   belirsizliği sürdürür) sermaye etkisi nedeniyle bu turda da tek taraflı
   yapılmadı — 113. turda zaten iletildi.
2. **Zamanlama sıklığı** (106. turdan beri açık, yukarıda tekrar
   doğrulandı) — hesap seviyesinde bir zamanlayıcı ayarı, bu oturumdan
   değiştirilemiyor.
- Sandbox'ta hâlâ canlı bot örneği yok: `data/control.json`,
  `data/status.json`, `data/positions.json` mevcut değil. %10 sermaye
  hedefine karşı bu turdan da doğrudan ölçülebilir ilerleme sağlanamadı.

Tam test paketi kökten çalıştırıldı (`python3 -m pip install -r
requirements.txt` sonrası `python3 -m pytest`): **1790 passed, 4
skipped** — regresyon yok, önceki turlarla birebir aynı sonuç. Test
sonrası `git status --short` temiz (state-leak yok).

## Bu turda yeni bulgu
Kod/davranış değişikliği yok. Bulgu (1)'in karakterizasyonu bu turda
birincil kaynaklardan (incident postmortem + operational policy)
derinleştirildi, ama sonuç aynı: kullanıcı kararı gerektiren, sermaye
etkili, belirsiz bir mimari soru — yeni bir "live bug" değil.

## Bildirim kararı
Bulgularda gerçek bir değişiklik yok; 113. turda zaten iletildi ve
114-126. turlarda "yeni bilgi yoksa bildirme" politikasıyla tutarlı
şekilde tekrar bildirilmedi. Bu turdaki ek derinlik (incident postmortem
doğrulaması) mevcut bulgunun netliğini artırdı ama yeni bir karar
gerektirmiyor — politika bu turda da uygulandı, ayrı bir push bildirimi
gönderilmedi.

## Sonuç
Kod tabanı sağlıklı (1790/1790, regresyon yok), yeni bir live bug yok.
Açık aksiyon kalemleri (onay kuyruğu/doğrudan emir çelişkisinin hangi
yönde çözüleceği, rutin tetikleyicisinin hesap seviyesinde günlük aralığa
çekilmesi, canlı pozisyon verisinin bu sandbox'a bağlanıp bağlanmayacağı)
değişmeden kullanıcı kararını bekliyor.
