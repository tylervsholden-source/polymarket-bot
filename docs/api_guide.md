# API Kurulum Rehberi

## 1. Anthropic API

1. https://console.anthropic.com adresine git
2. API Keys → Create Key
3. `.env` dosyasına ekle: `ANTHROPIC_API_KEY=sk-ant-...`

## 2. Polymarket API

### Adımlar
1. https://polymarket.com adresine git, cüzdan bağla
2. Profil → API Keys → Generate
3. 3 değeri kopyala: `API_KEY`, `SECRET`, `PASSPHRASE`
4. `.env` dosyasına doldur

### CLOB API Endpoint
```
Base URL: https://clob.polymarket.com
Gamma (Market Data): https://gamma-api.polymarket.com
```

### Önemli Endpointler
| Endpoint | Metod | Açıklama |
|----------|-------|----------|
| /markets | GET | Aktif marketler |
| /markets/{id} | GET | Tek market detayı |
| /order | POST | Emir ver |
| /order/{id} | GET | Emir durumu |
| /trades | GET | İşlem geçmişi |

## 3. Cüzdan Kurulumu

Polymarket Polygon ağı kullanır:
- Minimum: MetaMask veya başka EVM cüzdan
- USDC (Polygon) gerekli
- `POLYMARKET_WALLET_ADDRESS` ve `POLYMARKET_PRIVATE_KEY` `.env`'e ekle

## 4. Test

```bash
# Önce simülasyon modunda test et (API key olmadan da çalışır)
python main.py --backtest

# Status kontrol
python main.py --status
```

## 5. Sorun Giderme

| Hata | Çözüm |
|------|-------|
| `401 Unauthorized` | API key yanlış/eksik |
| `insufficient funds` | USDC bakiyesi yetersiz |
| `market not active` | Market kapanmış |
| Rate limit | `CYCLE_INTERVAL_SECONDS` artır |
