"""
Backward-compat shim — gerçek implementasyon control_plane/approval_queue.py'de.

Bu dosya eski import'ları kırmamak için yönlendirme yapar:
  from core.approval_queue import enqueue, approve, ...
"""
from control_plane.approval_queue import (  # noqa: F401
    enqueue,
    approve,
    reject,
    get_approved,
    get_pending,
    get_all,
    mark_executed,
    cleanup_expired,
    block_execution,
)
