__all__ = ["StoreBundle", "StoreProvider"]


def __getattr__(name: str):
    if name in {"StoreBundle", "StoreProvider"}:
        from .provider import StoreBundle, StoreProvider

        return {"StoreBundle": StoreBundle, "StoreProvider": StoreProvider}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
