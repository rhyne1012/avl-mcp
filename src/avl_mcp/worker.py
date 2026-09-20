"""Internal detached worker entry point. Work is described by an immutable job specification."""

import signal
import sys

from .jobs import run_worker

if __name__ == "__main__":

    def interrupted(signum, frame):
        # Unwind the native-session finally block before dropping the worker lock.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        run_worker(*sys.argv[1:])
    except KeyboardInterrupt:
        pass
