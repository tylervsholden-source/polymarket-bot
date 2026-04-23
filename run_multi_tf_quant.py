import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

def fetch_data(symbol='BTCUSDT', interval='5m', days=365):
    base_url = "https://api.binance.com/api/v3/klines"
    limit = 1000
    
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = now_ms - (days * 24 * 60 * 60 * 1000)
    
    # Calculate interval in ms
    unit = interval[-1]
    value = int(interval[:-1])
    if unit == 'm':
        interval_ms = value * 60 * 1000
    elif unit == 'h':
        interval_ms = value * 60 * 60 * 1000
    else:
        interval_ms = 300_000
        
    all_klines = []
    
    for i in range(200):
        url = f"{base_url}?symbol={symbol}&interval={interval}&limit={limit}&startTime={start_time}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read())
            
            if not data:
                break
            
            all_klines.extend(data)
            start_time = data[-1][0] + interval_ms
            
            if start_time > now_ms:
                break
                
            time.sleep(0.03)
        except Exception as e:
            print(f"Error fetching batch {i} for {interval}: {e}")
            break
            
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

def analyze_interval(data, interval, settings):
    res = []
    res.append(f"==================================================")
    res.append(f"  ZAMAN DILIMI: {interval} | Mum Sayisi: {len(data)}")
    res.append(f"==================================================")
    
    if len(data) < 50:
        res.append("Hata: Yeterli veri yok.")
        return res
        
    mom_th = settings['mom']
    vol_th = settings['vol_multiplier']
    
    up_mom_count = 0
    up_mom_wins = 0      
    up_mom_ret_sum = 0
    
    down_mom_count = 0
    down_mom_wins = 0    
    down_mom_ret_sum = 0

    vol_spike_up_count = 0
    vol_spike_up_drops = 0 

    vol_spike_down_count = 0
    vol_spike_down_bounces = 0 

    for i in range(20, len(data) - 3):
        curr = data[i]
        prev = data[i-1]
        
        ret = (curr['close'] - prev['close']) / prev['close']
        
        future = data[i+3]
        next_3_ret = (future['close'] - curr['close']) / curr['close']
        
        # Momentum check
        if ret > mom_th:
            up_mom_count += 1
            if next_3_ret > 0:
                up_mom_wins += 1
            up_mom_ret_sum += next_3_ret
            
        elif ret < -mom_th:
            down_mom_count += 1
            if next_3_ret < 0:
                down_mom_wins += 1
            down_mom_ret_sum += next_3_ret
            
        # Volume Spike check (SMA 20)
        vol_sma_20 = sum(d['vol'] for d in data[i-20:i]) / 20.0
        if curr['vol'] > vol_sma_20 * vol_th:
            if ret > (mom_th * 0.5): # Significant spike up
                vol_spike_up_count += 1
                if next_3_ret < 0:
                    vol_spike_up_drops += 1
            elif ret < -(mom_th * 0.5): # Significant spike down
                vol_spike_down_count += 1
                if next_3_ret > 0:
                    vol_spike_down_bounces += 1

    res.append(f"[ Insight 1: Momentum Continuation (Trend Devami) ]")
    res.append(f"Esik Deger: Mumu > %{mom_th*100:.2f} asan artislar")
    if up_mom_count > 0:
        res.append(f"- Bullish Momentum (Sonraki 3 Mum Yukselis ORANI): %{(up_mom_wins/up_mom_count)*100:.2f} (Örneklem: {up_mom_count})")
    if down_mom_count > 0:
        res.append(f"- Bearish Momentum (Sonraki 3 Mum Dusus ORANI): %{(down_mom_wins/down_mom_count)*100:.2f} (Örneklem: {down_mom_count})")

    res.append(f"\n[ Insight 2: Volume Spike Mean Reversion (Tuzak / Sekme) ]")
    res.append(f"Esik Deger: Hacim SMA20'yi {vol_th}x kaskat asan mumlar")
    if vol_spike_up_count > 0:
        res.append(f"- Bullish Fakeout (Hacimli Yukselis sonrasi DUSUS Ihtimali): %{(vol_spike_up_drops/vol_spike_up_count)*100:.2f} (Örneklem: {vol_spike_up_count})")
    if vol_spike_down_count > 0:
        res.append(f"- Bearish Fakeout (Hacimli Dusus sonrasi YUKSELIS Ihtimali): %{(vol_spike_down_bounces/vol_spike_down_count)*100:.2f} (Örneklem: {vol_spike_down_count})")
    res.append("")
    return res

if __name__ == '__main__':
    intervals = {
        '5m':  {'mom': 0.003, 'vol_multiplier': 3.0},
        '15m': {'mom': 0.006, 'vol_multiplier': 2.5},
        '1h':  {'mom': 0.012, 'vol_multiplier': 2.0},
        '4h':  {'mom': 0.025, 'vol_multiplier': 1.8},
    }
    
    all_results = []
    all_results.append("COKLU ZAMAN DILIMI (MULTI-TF) 1-YILLIK BTCUSDT QUANT ANALIZI")
    
    for inv, settings in intervals.items():
        print(f"Fetching {inv} data...")
        data = fetch_data(interval=inv, days=365)
        print(f"Analyzing {inv}...")
        res = analyze_interval(data, inv, settings)
        all_results.extend(res)
        
    with open("quant_multi_tf_results.txt", "w", encoding='utf-8') as f:
        f.write("\n".join(all_results))
        
    print("Done! Saved to quant_multi_tf_results.txt")
