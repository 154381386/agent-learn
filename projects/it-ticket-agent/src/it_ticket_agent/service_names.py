from __future__ import annotations

from typing import Iterable


SERVICE_NAME_ALIASES = {
    "车云服务": "车云服务",
    "car-cloud-service": "车云服务",
    "car-cloud": "车云服务",
    "支付服务": "支付服务",
    "payment-service": "支付服务",
    "payment service": "支付服务",
    "payment": "支付服务",
    "订单服务": "order-service",
    "order-service": "order-service",
    "order service": "order-service",
    "checkout-service": "order-service",
    "checkout service": "order-service",
}


def canonical_service_name(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    return SERVICE_NAME_ALIASES.get(lowered, SERVICE_NAME_ALIASES.get(normalized, normalized))


def infer_service_name(message: str | None, *, candidates: Iterable[str] | None = None) -> str:
    haystack = str(message or "").strip().lower()
    if not haystack:
        return ""
    names = set(SERVICE_NAME_ALIASES.keys())
    if candidates:
        names.update(str(item).strip().lower() for item in candidates if str(item).strip())
    for alias in sorted(names, key=len, reverse=True):
        if alias and alias in haystack:
            return canonical_service_name(alias)
    return ""
