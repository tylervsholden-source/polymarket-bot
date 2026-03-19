# Sistem Mimarisi (Guncellendi: Mart 2026)

## Canli Motor: agents/orchestrator.py

ORCHESTRATOR (60sn dongu)
    | BinanceFeed/Bitstamp -> spot, RSI, hacim, OB
    | ArbitrageEngine -> BayesianEstimator + EdgeModel + Stoikov + Kelly
    |   direction=YES -> yes_token_id  /  direction=NO -> no_token_id
    | SmartTraderTracker -> +/-0.05 boost
    | PositionManager -> capital, YES/NO PnL (double-count duzeltildi)
    | StatusWriter/WebServer -> dashboard

## DEVRE DISI (belgede var, canli kodda yok)

- Claude AI SignalAgent -> Bayesian ile degistirildi
- WhaleTracker -> SmartTraderTracker ile degistirildi
- Torture agent -> optional (anthropic yoksa devre disi)
- Backtest sonuclari -> endpoint-bias, guvenilmez

## Market Filtresi

Sadece: bitcoin/eth/sol/xrp/doge/bnb/hype up-or-down marketleri.
Oscar/sports/politika/oyun: KAPSAM DISI.
Mean reversion: DEVRE DISI.

## Pozisyon Muhasebesi

available_capital = capital - sum(open_pos.amount)
Kapanista: capital += pnl  (eski: += amount+pnl -> double count -> DUZELTILDI)
YES PnL: shares=amount/entry; value=shares*YES_current
NO PnL:  shares=amount/entry; value=shares*(1-YES_ask)

## Kontrol Dosyalari

data/control.json   : {live_trading, simulation_running, min_bet}
data/status.json    : dashboard canli durum
data/positions.json : {capital, positions, closed, daily}

min_bet: dashboard 1/5/10/20$ -> orchestrator okur -> bet_size = max(min_bet, kelly)

## Backtest Sinirlamalari

1. Entry = kapanisa yakin son fiyat (endpoint bias)
2. Tarihi candle/OB/RSI yok -> neutral Bayesian prior
3. Whale = simulasyon
Gercek dogrulama: sim modunda calistir, log izle.