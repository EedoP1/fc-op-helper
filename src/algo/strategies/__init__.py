"""Auto-discover all Strategy subclasses in this package."""
import importlib
import pkgutil
from src.algo.strategies.base import Strategy


def discover_strategies() -> dict[str, type[Strategy]]:
    """Import all modules in this package and return {name: class} for each Strategy subclass."""
    strategies = {}
    package_path = __path__
    for importer, modname, ispkg in pkgutil.iter_modules(package_path):
        if modname == "base":
            continue
        module = importlib.import_module(f"src.algo.strategies.{modname}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Strategy)
                and attr is not Strategy
                and hasattr(attr, "name")
            ):
                strategies[attr.name] = attr
    return strategies
