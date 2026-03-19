"""
Approval Queue: Emir onay mekanizması.

Akış:
1. Orchestrator sinyal bulunca → queue.enqueue(order_request)
2. Dashboard'da bekleyen emirler gösterilir
3. Kullanıcı onaylar → queue.approve(order_id)
4. Orchestrator her döngüde → queue.get_approved() → place_order()
5. Timeout (varsayılan 5dk) geçen emirler otomatik iptal

INC-2026-03-15-001 dersi: Sinyal → emir arasında insan onayı ZORUNLU.
"""
import json
import os
import time
from loguru import logger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING_FILE = os.path.join(_BASE_DIR, "data", "pending_orders.json")

# Onay olmadan geçecek max süre (saniye). Aşılırsa emir iptal.
DEFAULT_TIMEOUT_SEC = 300  # 5 dakika


def _load() -> list[dict]:
    try:
        with open(PENDING_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(orders: list[dict]):
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(orders, f, indent=2, ensure_ascii=False)


def enqueue(order_request: dict) -> str:
    """Bekleyen emirler kuyruğuna ekle. order_id döner."""
    orders = _load()

    # Aynı market_id zaten kuyrukta mı?
    market_id = order_request.get("market_id", "")
    for o in orders:
        if o.get("market_id") == market_id and o.get("status") == "pending":
            logger.debug(f"Market {market_id} zaten kuyrukta, atlanıyor.")
            return o["id"]

    import uuid
    order_id = str(uuid.uuid4())[:8]
    entry = {
        "id": order_id,
        "status": "pending",  # pending → approved → executed | expired | rejected
        "enqueued_at": time.time(),
        "market_id": market_id,
        "question": order_request.get("question", ""),
        "direction": order_request.get("direction", ""),
        "amount": order_request.get("amount", 0),
        "entry_price": order_request.get("entry_price", 0),
        "edge": order_request.get("edge", 0),
        "bayesian_prob": order_request.get("bayesian_prob", 0),
        "token_id": order_request.get("token_id", ""),
    }
    orders.append(entry)
    _save(orders)
    logger.info(f"Emir kuyruğa eklendi [{order_id}]: {entry['question'][:50]} ${entry['amount']:.2f}")
    return order_id


def approve(order_id: str) -> bool:
    """Bekleyen emri onayla."""
    orders = _load()
    for o in orders:
        if o["id"] == order_id and o["status"] == "pending":
            o["status"] = "approved"
            o["approved_at"] = time.time()
            _save(orders)
            logger.info(f"Emir onaylandı [{order_id}]: {o['question'][:50]}")
            return True
    return False


def reject(order_id: str) -> bool:
    """Bekleyen emri reddet."""
    orders = _load()
    for o in orders:
        if o["id"] == order_id and o["status"] == "pending":
            o["status"] = "rejected"
            _save(orders)
            logger.info(f"Emir reddedildi [{order_id}]: {o['question'][:50]}")
            return True
    return False


def get_approved() -> list[dict]:
    """Onaylanmış ama henüz execute edilmemiş emirleri döner."""
    orders = _load()
    return [o for o in orders if o["status"] == "approved"]


def mark_executed(order_id: str):
    """Emri execute edildi olarak işaretle."""
    orders = _load()
    for o in orders:
        if o["id"] == order_id:
            o["status"] = "executed"
            o["executed_at"] = time.time()
    _save(orders)


def get_pending() -> list[dict]:
    """Onay bekleyen emirleri döner (dashboard için)."""
    return [o for o in _load() if o["status"] == "pending"]


def cleanup_expired(timeout_sec: float = DEFAULT_TIMEOUT_SEC):
    """Süresi dolmuş bekleyen emirleri iptal et."""
    orders = _load()
    now = time.time()
    changed = False
    for o in orders:
        if o["status"] == "pending" and (now - o.get("enqueued_at", 0)) > timeout_sec:
            o["status"] = "expired"
            changed = True
            logger.info(f"Emir süresi doldu [{o['id']}]: {o['question'][:50]}")
    if changed:
        _save(orders)


def get_all() -> list[dict]:
    """Tüm emirleri döner (dashboard tarihçe)."""
    return _load()
