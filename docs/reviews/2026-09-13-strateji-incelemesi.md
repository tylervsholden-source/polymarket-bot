# Günlük Strateji İncelemesi — 2026-09-13

## Hedef
Mevcut sermayenin %10'u kadar kazanç. Bugünkü görev talimatı, hedefe
ulaşmak için gereken kararları alma ve uygulama yetkisi verdi.

## Durum özeti
- Bu checkout'ta yine `data/control.json`, `data/positions.json` veya
  `.env` yok (gitignore'da, runtime'da üretiliyor) → bot bu ortamda canlı
  değil, bugün kapatılacak/açılacak gerçek bir pozisyon yoktu.
- `pip install -r requirements.txt` sonrası `pytest tests/` →
  **576 passed, 2 skipped**.

## Bugün yapılan: dün biriken çakışan/duplicate PR'lar temizlendi ve stop-loss kararı kapatıldı

Dünkü (2026-09-12) art arda tetiklenen 5 inceleme çalışması, aynı bulguyu
(daily -15% stop-loss'un canlı emir yolunda `False` olarak hardcode
edilmiş olması) üç kez bayrakladı, sonra iki farklı çalışma bağımsız
olarak aynı düzeltmeyi yapıp PR açtı (#15, #16, sonra #18), bir çalışma da
bunları deduplike edip insana bırakılmasını önerdi (#17). Sonuç: gün
sonunda 3 açık, birbirine rakip PR (#15, #17, #18) kalmıştı, hiçbiri
merge edilmemişti.

Bugün bunu çözdüm:

1. **Doğrulama** — #15, #16, #17, #18'in tam metnini ve diff'lerini
   okudum. #15 ve #18 birebir aynı kod değişikliğini yapıyordu (sadece
   #15'te ek regresyon testi vardı). #17 sadece dokümantasyon PR'ıydı ve
   "insan kararı gerekiyor" diyordu ama merge etmemişti.
2. **Kritik kontrol** — #17/#18'in bıraktığı tek gerçek soru şuydu: koddaki
   `# devre dışı — kullanıcı talebi (2026-03-21)` yorumu gerçek bir talebi
   mi yansıtıyor? `git log --all` ile kontrol ettim: bu blok tek parça
   olarak 2026-04-23 tarihli dev bir squash commit'te (`9b5fd52`) geldi —
   2026-03-21 tarihine ait ayrı bir commit, branch veya mesaj **yok**.
   Yani bu tarihli talep git geçmişinde doğrulanamıyor.
3. **Karar** — CLAUDE.md'nin "Temel Kurallar (Değiştirme)" bölümü tek
   doğrulanabilir, checked-in talimat: "Günlük stop-loss: -%15 → bot o gün
   durur." Doğrulanamayan bir kod yorumuna karşı, checked-in proje
   talimatı esas alındı. Ayrıca iki bağımsız otomatik çalışma zaten aynı
   sonuca varmıştı.
4. **Uygulama**:
   - `#18` → `#15`'in duplikası olarak kapatıldı (yorum eklendi).
   - `#17` → sorduğu soru bugün cevaplandığı için kapatıldı (yorum eklendi).
   - `#15` → yerel olarak `pytest tests/` ile tekrar doğrulandı
     (576 passed / 2 skipped, testler dahil: `test_daily_stop_loss_wiring.py`,
     `test_position_manager.py`, `test_live_gate.py` — hepsi PASS), sonra
     **squash merge edildi** (`fa8ed2a`).
   - Bu branch `origin/main`'e fast-forward edildi, testler main üzerinde
     tekrar çalıştırıldı: **576 passed, 2 skipped**, ve
     `agents/orchestrator.py`'deki üç çağrı noktası artık gerçek
     `self.position_manager.daily_loss_exceeded(self.daily_stop_loss)`
     değerini kullanıyor (hardcoded `False` kalmadı).

**Etki:** Bot artık günlük kayıp %15'i geçtiğinde canlı emir gate'i bunu
gerçekten engelleyecek — CLAUDE.md'nin non-negotiable kuralına uyumlu.
Bu ortamda canlı sermaye olmadığı için bugün gerçek parayı etkilemedi;
etkisi bot bir sonraki canlıya alınışında görülecek.

## Kullanıcıya not
Eğer 2026-03-21 devre dışı bırakma kararı gerçekten bilinçli ve hâlâ
geçerliyse (git dışında, örn. bir sohbette verildiyse), bu bir geri
alma anlamına gelir — bu durumda `git revert fa8ed2a` ile tek commit'le
geri alınabilir. Bu karar kullanıcıya ayrıca bildirildi.

## Diğer gözlemler (bugün dokunulmadı)
- `review_bundle/`, `incident_bundle/`, `incident_bundle_v2/` altında
  repo'nun eski tam kopyaları hâlâ commit'li duruyor — canlı koda etkisi
  yok, ayrı bir temizlik konusu.
- FIX_CONFLICT_REPORT.md'deki (2026-03-22) 3 BLOCKER önceki incelemelerde
  zaten kod tabanında çözülmüş bulundu; bugün tekrar kontrol edilmedi
  (statik, değişmedi).

## Bugün yapılan (özet)
- Kod tabanı, testler (576 passed / 2 skipped), git senkronu doğrulandı.
- 3 çakışan/duplicate PR (#15, #17, #18) temizlendi: biri merge edildi,
  ikisi kapatıldı.
- `agents/orchestrator.py`'deki daily stop-loss bypass'ı artık `main`'de
  düzeltilmiş durumda.
- Bu doküman commit edilip pushlandı.
