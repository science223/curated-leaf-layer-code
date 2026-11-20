def __getattr__(name):
    if name == "Voyager":
        from .voyager import Voyager

        return Voyager
    raise AttributeError(f"module 'voyager' has no attribute {name!r}")


__all__ = ["Voyager"]
