"""
Timer state machine for Pomodoro bot.

Each chat gets its own UserState that tracks:
  - selected mode
  - current phase  (idle / focus / short_break / long_break)
  - seconds remaining
  - completed focus-session count
  - lifetime focus-minutes

The running countdown is kept as an asyncio Task stored on the object;
cancellation / replacement is handled here so bot.py stays clean.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Mode definitions
# ──────────────────────────────────────────────────────────────────────────────

MODES: dict[str, dict] = {
    "classic": {
        "name": "🍅 Классическое",
        "desc": "25 мин фокус · 5 мин перерыв · 15 мин длинный",
        "focus": 25,
        "short_break": 5,
        "long_break": 15,
        "sessions_before_long": 4,
    },
    "long": {
        "name": "🎯 Длинный фокус",
        "desc": "50 мин фокус · 10 мин перерыв · 30 мин длинный",
        "focus": 50,
        "short_break": 10,
        "long_break": 30,
        "sessions_before_long": 3,
    },
    "short": {
        "name": "⚡ Быстрый спринт",
        "desc": "15 мин фокус · 3 мин перерыв · 10 мин длинный",
        "focus": 15,
        "short_break": 3,
        "long_break": 10,
        "sessions_before_long": 4,
    },
    "deep": {
        "name": "🧘 Глубокая работа",
        "desc": "90 мин фокус · 20 мин перерыв · 40 мин длинный",
        "focus": 90,
        "short_break": 20,
        "long_break": 40,
        "sessions_before_long": 2,
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Phase → display strings
# ──────────────────────────────────────────────────────────────────────────────

PHASE_LABELS = {
    "focus":       "🍅 Фокус",
    "short_break": "☕ Перерыв",
    "long_break":  "🌙 Длинный перерыв",
    "idle":        "😴 Пауза",
}


def fmt_time(seconds: int) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(max(0, seconds), 60)
    return f"{m:02d}:{s:02d}"


def progress_bar(progress: float, width: int = 12) -> str:
    """Return a Unicode block progress bar."""
    filled = round(progress * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(progress * 100)
    return f"[{bar}] {pct}%"


# ──────────────────────────────────────────────────────────────────────────────
# Per-user state
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class UserState:
    mode_key: str = "classic"

    # Current phase
    phase: str = "idle"          # idle | focus | short_break | long_break
    seconds_remaining: int = 0
    total_seconds: int = 0

    # Session tracking
    session_count: int = 0       # completed focus sessions (total)
    sessions_this_cycle: int = 0 # within current long-break cycle
    total_focus_minutes: int = 0

    # Telegram message IDs for in-place editing
    status_message_id: Optional[int] = None
    photo_message_id: Optional[int] = None

    # Running asyncio task
    _task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    # ── helpers ──────────────────────────────────────────────────────────────

    @property
    def mode(self) -> dict:
        return MODES[self.mode_key]

    @property
    def progress(self) -> float:
        if self.total_seconds == 0:
            return 0.0
        return 1.0 - self.seconds_remaining / self.total_seconds

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def sessions_before_long(self) -> int:
        return self.mode["sessions_before_long"]

    def cancel_task(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def next_break_type(self) -> str:
        """Decide whether the next break should be short or long."""
        sbl = self.mode["sessions_before_long"]
        if (self.sessions_this_cycle + 1) >= sbl:
            return "long_break"
        return "short_break"

    def phase_duration(self, phase: str) -> int:
        """Return duration in seconds for a given phase key."""
        minutes = self.mode[phase]   # phase key matches mode dict key
        return minutes * 60

    def start_phase(self, phase: str):
        """Configure state for a new phase (does NOT spawn the task)."""
        self.phase = phase
        self.total_seconds = self.phase_duration(phase)
        self.seconds_remaining = self.total_seconds

    def on_focus_complete(self):
        self.session_count += 1
        self.sessions_this_cycle += 1
        self.total_focus_minutes += self.mode["focus"]
        # Reset cycle counter after long break threshold
        if self.sessions_this_cycle >= self.mode["sessions_before_long"]:
            self.sessions_this_cycle = 0

    def reset(self):
        self.cancel_task()
        self.phase = "idle"
        self.seconds_remaining = 0
        self.total_seconds = 0
        self.status_message_id = None
        self.photo_message_id = None


# ──────────────────────────────────────────────────────────────────────────────
# Global registry
# ──────────────────────────────────────────────────────────────────────────────

_states: dict[int, UserState] = {}


def get_state(chat_id: int) -> UserState:
    if chat_id not in _states:
        _states[chat_id] = UserState()
    return _states[chat_id]


# ──────────────────────────────────────────────────────────────────────────────
# Countdown coroutine factory
# ──────────────────────────────────────────────────────────────────────────────

async def run_countdown(
    state: UserState,
    on_tick: Callable[[UserState], Awaitable[None]],
    on_finish: Callable[[UserState], Awaitable[None]],
    tick_interval: int = 60,
):
    """
    Decrement state.seconds_remaining every second.
    Call on_tick every tick_interval seconds (default: 1 minute).
    Call on_finish when the countdown reaches 0.
    """
    try:
        ticks_since_update = 0
        while state.seconds_remaining > 0:
            await asyncio.sleep(1)
            state.seconds_remaining -= 1
            ticks_since_update += 1

            if ticks_since_update >= tick_interval or state.seconds_remaining == 0:
                ticks_since_update = 0
                if state.seconds_remaining > 0:
                    await on_tick(state)

        await on_finish(state)

    except asyncio.CancelledError:
        logger.info("Timer cancelled for phase=%s", state.phase)
