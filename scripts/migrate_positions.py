"""
positions.json state migration + temizlik.

Yapar:
  1. outcome normalizasyonu: UP -> YES, DOWN -> NO
  2. duplicate kapalı kayıtları siler (order_id bazlı deduplikasyon)
  3. legacy alanları kaldırır: resolved_yes, mr_type, target_price
  4. -0.0 pnl -> 0.0, "LOSS" when pnl==-0.0 -> "NEUTRAL"
  5. Non-crypto closed betleri (Oscar, sports, LOL, CS...) temizler
  6. Otomatik backup: positions.json.bak

Kullanım:
  python scripts/migrate_positions.py [--reset-capital]
  --reset-capital: capital'ı INITIAL_CAPITAL'a resetler
"""
import json, os, shutil, sys, argparse
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "positions.json"
BACKUP    = ROOT / "data" / "positions.json.bak"

LEGACY_FIELDS = {"resolved_yes", "mr_type", "target_price", "current_price",
                 "current_value", "unrealized_pnl", "end_date_iso"}

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "xrp", "ripple", "dogecoin", "doge", "bnb", "hyperliquid", "hype",
    "up or down",
]

def is_crypto(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in CRYPTO_KEYWORDS)


def normalize_outcome(outcome: str) -> str:
    mapping = {"UP": "YES", "DOWN": "NO", "up": "YES", "down": "NO"}
    return mapping.get(outcome, outcome)


def fix_pnl(entry: dict) -> dict:
    """Fix -0.0 -> 0.0 and wrong result label."""
    pnl = entry.get("pnl", 0)
    if pnl == 0:
        entry["pnl"] = 0.0
        if entry.get("result") == "LOSS":
            entry["result"] = "NEUTRAL"
    return entry


def remove_legacy(entry: dict) -> dict:
    for f in LEGACY_FIELDS:
        entry.pop(f, None)
    return entry


def clean_positions(positions: dict) -> dict:
    """Normalize active positions."""
    cleaned = {}
    for pid, pos in positions.items():
        pos["outcome"] = normalize_outcome(pos.get("outcome", "YES"))
        pos = remove_legacy(pos)
        cleaned[pid] = pos
    return cleaned


def clean_closed(closed: list) -> list:
    """Deduplicate + normalize + remove non-crypto + fix pnl."""
    seen_ids = set()
    result = []
    for entry in closed:
        oid = entry.get("order_id", "")
        question = entry.get("question", "")

        # Dedup by order_id
        if oid and oid in seen_ids:
            continue
        if oid:
            seen_ids.add(oid)

        # Only keep crypto-related closed positions
        if not is_crypto(question):
            continue

        entry["outcome"] = normalize_outcome(entry.get("outcome", "YES"))
        entry = remove_legacy(entry)
        entry = fix_pnl(entry)
        result.append(entry)

    return result


def migrate(reset_capital: bool = False):
    if not DATA_FILE.exists():
        print("positions.json bulunamadı. Yeni dosya oluşturuluyor.")
        clean = {
            "capital": float(os.getenv("INITIAL_CAPITAL", 3.59)),
            "positions": {},
            "closed": [],
            "daily": {"date": str(date.today()), "pnl": 0.0},
        }
        DATA_FILE.parent.mkdir(exist_ok=True)
        DATA_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False))
        print("Temiz positions.json oluşturuldu.")
        return

    # Backup
    shutil.copy2(DATA_FILE, BACKUP)
    print(f"Backup: {BACKUP}")

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    capital_before = data.get("capital", 0)
    positions_before = len(data.get("positions", {}))
    closed_before = len(data.get("closed", []))

    # Migrate
    data["positions"] = clean_positions(data.get("positions", {}))
    data["closed"] = clean_closed(data.get("closed", []))

    # Fix daily pnl float (-0.0 / floating point artifacts)
    daily_pnl = data.get("daily", {}).get("pnl", 0)
    if abs(daily_pnl) < 1e-10:
        daily_pnl = 0.0
    data["daily"] = {"date": str(date.today()), "pnl": daily_pnl}

    if reset_capital:
        initial = float(os.getenv("INITIAL_CAPITAL", 3.59))
        data["capital"] = initial
        print(f"Capital reset: {capital_before:.6f} -> {initial:.6f}")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nMigration tamamlandı:")
    print(f"  Capital        : {capital_before:.6f} -> {data['capital']:.6f}")
    print(f"  Açık pozisyon  : {positions_before} -> {len(data['positions'])}")
    print(f"  Closed         : {closed_before} -> {len(data['closed'])}  "
          f"(kaldırılan: {closed_before - len(data['closed'])} duplicate/non-crypto)")
    print(f"  Daily PnL      : {daily_pnl:.6f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-capital", action="store_true",
                        help="capital'ı INITIAL_CAPITAL env'e resetle")
    args = parser.parse_args()
    migrate(reset_capital=args.reset_capital)
