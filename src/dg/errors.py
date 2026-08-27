class DgError(Exception):
    """Operational error. CLI prints `error: <msg>` and exits 1; never a traceback."""
