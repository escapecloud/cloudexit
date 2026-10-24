__all__ = ["main", "run"]


def __getattr__(name):
    # The application module imports the engine, and the engine's providers use
    # the prompt helpers in this package; importing it only on first use keeps
    # `import cloudexit.cli.prompts` from pulling the whole application in.
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
