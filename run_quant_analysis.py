import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

def fetch_data(symbol='BTCUSDT', interval='5m', days=365):
    # Binance klines endpoint
    base_url = "https://api.binance.com/api/v3/klines"
    limit = 1000
    
    # 365 days ago in ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = now_ms - (days * 24 * 60 * 60 * 1000)
    
    # 5m in ms = 300,000
    interval_ms = 300_000
    
    all_klines = []
    
    # ~106 requests for 1 year
    for i in range(120):
        url = f"{base_url}?symbol={symbol}&interval={interval}&limit={limit}&startTime={start_time}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read())
            
            if not data:
                break
            
            all_klines.extend(data)
            # Next start time = last candle's open time + 1 interval
            start_time = data[-1][0] + interval_ms
            
            if start_time > now_ms:
                break
                
            time.sleep(0.05)
        except Exception as e:
            print(f"Error fetching batch {i}: {e}")
            break
            
    # Format: [open_time, open, high, low, close, volume, ...]
    formatted = []
    for k in all_klines:
        formatted.append({
            'ts': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'vol': float(k[5])
        })
    return formatted

def analyze(data):
    results = []
    results.append("==================================================")
    results.append("BINANCE 1-YILLIK 5m KLINE KUANTITATIF (QUANT) ANALIZI")
    results.append(f"Toplam Mum Sayisi (5m): {len(data)}")
    results.append("==================================================")
    
    if len(data) < 100:
        results.append("Hata: Yeterli veri cekilemedi.")
        with open("quant_results.txt", "w", encoding='utf-8') as f:
            f.write("\n".join(results))
        return

    # 1. Momentum Continuity
    up_mom_count = 0
    up_mom_wins = 0      # next 15 mins closes higher
    up_mom_ret_sum = 0
    
    down_mom_count = 0
    down_mom_wins = 0    # next 15 mins closes lower
    down_mom_ret_sum = 0

    # 2. Volume Spike Mean Reversion
    # To compute 20-sma volume, we need window
    vol_spike_up_count = 0
    vol_spike_up_drops = 0 # Drop in next 15 mins

    vol_spike_down_count = 0
    vol_spike_down_bounces = 0 # Bounce in next 15 mins

    # 3. Time of day
    hourly_ret_sums = {h: 0.0 for h in range(24)}
    hourly_counts = {h: 0 for h in range(24)}

    # Loop through to compute
    for i in range(20, len(data) - 3):
        curr = data[i]
        prev = data[i-1]
        
        # pct return
        ret = (curr['close'] - prev['close']) / prev['close']
        
        # next 3 candle return (15 mins)
        future = data[i+3]
        next_3_ret = (future['close'] - curr['close']) / curr['close']
        
        # hourly
        dt = datetime.fromtimestamp(curr['ts'] / 1000.0, tz=timezone.utc)
        hourly_ret_sums[dt.hour] += ret
        hourly_counts[dt.hour] += 1
        
        # Momentum check
        if ret > 0.003: # +0.3%
            up_mom_count += 1
            if next_3_ret > 0:
                up_mom_wins += 1
            up_mom_ret_sum += next_3_ret
            
        elif ret < -0.003: # -0.3%
            down_mom_count += 1
            if next_3_ret < 0:
                down_mom_wins += 1
            down_mom_ret_sum += next_3_ret
            
        # Volume Spike check (SMA 20)
        vol_sma_20 = sum(d['vol'] for d in data[i-20:i]) / 20.0
        if curr['vol'] > vol_sma_20 * 3:
            if ret > 0.002: # Spike up
                vol_spike_up_count += 1
                if next_3_ret < 0:
                    vol_spike_up_drops += 1
            elif ret < -0.002: # Spike down
                vol_spike_down_count += 1
                if next_3_ret > 0:
                    vol_spike_down_bounces += 1

    # Format Results
    results.append(f"\n[ Insight 1: Momentum Continuation (BULLISH) ]")
    results.append("Soru: Bir 5dk'lık mum %0.3'ten fazla yükselirse, trend devam eder mi?")
    results.append(f"- > %0.3 Artan güçlü mum sayısı: {up_mom_count}")
    if up_mom_count > 0:
        results.append(f"- Sonraki 15dk içinde fiyatın DAHA DA ARTMASI (Win Rate): {(up_mom_wins/up_mom_count)*100:.2f}%")
        results.append(f"- Sonraki 15dk Ortalama Edge (Getiri): %{(up_mom_ret_sum/up_mom_count)*100:.3f}")
    
    results.append(f"\n[ Insight 2: Momentum Continuation (BEARISH) ]")
    results.append("Soru: Bir 5dk'lık mum %0.3'ten fazla düşerse, düşüş sürer mi?")
    results.append(f"- < -%0.3 Düşen güçlü mum sayısı: {down_mom_count}")
    if down_mom_count > 0:
        results.append(f"- Sonraki 15dk içinde fiyatın DAHA DA DÜŞMESİ (Win Rate): {(down_mom_wins/down_mom_count)*100:.2f}%")
        results.append(f"- Sonraki 15dk Ortalama Edge (Getiri): %{(down_mom_ret_sum/down_mom_count)*100:.3f}")

    results.append(f"\n[ Insight 3: Volume Spike Mean Reversion ]")
    results.append("Soru: Hacim %300 artarak mum yön değiştirdiğinde bu bir Tuzak (Fakeout) mıdır?")
    results.append(f"- BÜYÜK HACİMLİ Yükseliş Mumu Sayısı: {vol_spike_up_count}")
    if vol_spike_up_count > 0:
        results.append(f"- Sonraki 15dk Fiyatın GERİ ÇEKİLMESİ (Tuzak) Olasılığı: {(vol_spike_up_drops/vol_spike_up_count)*100:.2f}%")
    
    results.append(f"- BÜYÜK HACİMLİ Düşüş Mumu Sayısı: {vol_spike_down_count}")
    if vol_spike_down_count > 0:
        results.append(f"- Sonraki 15dk Fiyatın YUKARI SEKMESİ (Bounce) Olasılığı: {(vol_spike_down_bounces/vol_spike_down_count)*100:.2f}%")

    # Time of Day
    hourly_avg = {}
    for h in range(24):
        if hourly_counts[h] > 0:
            # Multiply by 12 points per hour * 365 to get generalized annualized look, or just raw avg
            hourly_avg[h] = (hourly_ret_sums[h] / hourly_counts[h]) * 100 * 12 # hourly percentage momentum
        else:
            hourly_avg[h] = 0.0
            
    best_hr = max(hourly_avg, key=hourly_avg.get)
    worst_hr = min(hourly_avg, key=hourly_avg.get)
    
    results.append(f"\n[ Insight 4: Time of Day Bias (Günün Saatlerine Göre Kripto Yönü - UTC) ]")
    results.append(f"- En Bullish Saat (Ortalama): {best_hr}:00 UTC (Saatlik ivme: +{hourly_avg[best_hr]:.3f}%)")
    results.append(f"- En Bearish Saat (Ortalama): {worst_hr}:00 UTC (Saatlik ivme: {hourly_avg[worst_hr]:.3f}%)")
    
    results.append("\n==================================================")
    
    with open("quant_results.txt", "w", encoding='utf-8') as f:
        f.write("\n".join(results))
    print("Done! Saved to quant_results.txt")

if __name__ == '__main__':
    print("Fetching 1 Year of 5m BTCUSDT data from Binance API directly (no pandas/ccxt needed)...")
    data = fetch_data(days=365)
    print("Analyzing quant edges...")
    analyze(data)
