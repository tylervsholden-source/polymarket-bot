---
description: Polymarket Bot Kontrol Komutları
---
// turbo-all

Bu dosya içindeki komutlar otomatik olarak onaylanmış sayılır.

1. Botu Başlat
python main.py

2. Testleri Çalıştır
python -m pytest tests/test_torture_agent.py tests/test_dashboard.py

3. Sistem Kontrolü
python run_tests.py

4. Açık Pozisyonları Kapat
python close_all_positions.py
