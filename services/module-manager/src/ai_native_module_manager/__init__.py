__all__ = ["ModuleProcessError", "ModuleProcessManager"]


def __getattr__(name: str):
    if name in __all__:
        from .manager import ModuleProcessError, ModuleProcessManager

        return {"ModuleProcessError": ModuleProcessError, "ModuleProcessManager": ModuleProcessManager}[name]
    raise AttributeError(name)
