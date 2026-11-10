"""Lightweight per-client login throttling for CampusFix.

Failed sign-in attempts are counted per *client* (the Streamlit browser
session), never per account. A client that fails too often in the window
must wait before trying again, but this never affects other people: no one
can lock or slow down someone else's account by entering wrong passwords
for their email.

Limitations (prototype)
-----------------------
* State is kept in this process's memory, so it resets when the app
  restarts and is not shared between multiple app processes.
* A determined attacker can open new browser sessions to get a fresh
  allowance. Argon2id verification cost still slows every guess.

For a production deployment, add rate limiting at a reverse proxy or API
gateway keyed by client IP/device (with care for shared campus NAT
addresses), plus CAPTCHA and/or multi-factor authentication. Avoid
account-wide lockout, which enables denial of service against users.
"""

import threading
import time
from collections import deque

MAX_FAILURES = 5
WINDOW_SECONDS = 5 * 60
_MAX_TRACKED_CLIENTS = 10_000


class LoginRateLimiter:
    """Sliding-window limit on failed attempts per client key."""

    def __init__(self, max_failures=MAX_FAILURES,
                 window_seconds=WINDOW_SECONDS, clock=time.monotonic):
        self.max_failures = int(max_failures)
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._failures = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] >= self.window_seconds:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def retry_after(self, key):
        """Seconds the client must wait before trying again (0 = allowed)."""
        with self._lock:
            now = self._clock()
            attempts = self._prune(key, now)
            if attempts is None or len(attempts) < self.max_failures:
                return 0
            return max(1, int(self.window_seconds - (now - attempts[0]) + 0.999))

    def record_failure(self, key):
        with self._lock:
            now = self._clock()
            if (
                key not in self._failures
                and len(self._failures) >= _MAX_TRACKED_CLIENTS
            ):
                for stale in list(self._failures):
                    self._prune(stale, now)
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)


# Shared by all sessions of this app process.
login_limiter = LoginRateLimiter()
