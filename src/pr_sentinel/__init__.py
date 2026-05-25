try:
    from importlib.metadata import version, PackageNotFoundError
    __version__ = version("pr-sentinel")
except PackageNotFoundError:
    __version__ = "unknown"
