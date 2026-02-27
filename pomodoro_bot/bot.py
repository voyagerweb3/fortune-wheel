"""
Pomodoro Focus-Timer Telegram Bot
==================================
Features:
  - 4 Pomodoro modes (Classic / Long / Short Sprint / Deep Work)
  - Beautiful hourglass images at every phase transition
  - Sand-flowing start sound, bell chime at completion
  - Live text countdown (updated every minute via message edit)
  - Session counter with dot indicators
  - /stats command for lifetime stats

Setup:
  1. pip install -r requirements.txt
  2. cp .env.example .env  →  set BOT_TOKEN=<your token>
  3. python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO

from dotenv import load_dotenv
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from audio import get_sound
from timer import (
    MODES,
    PHASE_LABELS,
    UserState,
    fmt_time,
    get_state,
    progress_bar,
    run_countdown,
)
from visuals import create_hourglass_image

load_dotenv()
logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")


# ──────────────────────────────────────────────────────────────────────────────
# Keyboard builders
# ──────────────────────────────────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️  Начать фокус", callback_data="start_focus")],
        [
            InlineKeyboardButton("🎛  Режим",  callback_data="choose_mode"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
        ],
    ])


def kb_mode_select() -> InlineKeyboardMarkup:
    rows = []
    for key, m in MODES.items():
        rows.append([InlineKeyboardButton(
            f"{m['name']}  ({m['desc'].split('·')[0].strip()})",
            callback_data=f"mode_{key}",
        )])
    rows.append([InlineKeyboardButton("⬅️  Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_running() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏹  Стоп",     callback_data="stop"),
        InlineKeyboardButton("⏭  Пропустить", callback_data="skip"),
    ]])


def kb_after_focus(next_break: str) -> InlineKeyboardMarkup:
    break_label = "🌙 Длинный перерыв" if next_break == "long_break" else "☕ Короткий перерыв"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️  {break_label}", callback_data=f"start_{next_break}")],
        [
            InlineKeyboardButton("🚫 Пропустить перерыв", callback_data="start_focus"),
            InlineKeyboardButton("📊 Стоп", callback_data="stop"),
        ],
    ])


def kb_after_break() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️  Новый фокус",      callback_data="start_focus")],
        [InlineKeyboardButton("📊 Стоп / статистика", callback_data="stop")],
    ])


# ──────────────────────────────────────────────────────────────────────────────
# Message text helpers
# ──────────────────────────────────────────────────────────────────────────────

def _mode_summary(state: UserState) -> str:
    m = state.mode
    return (
        f"<b>{m['name']}</b>\n"
        f"<i>{m['desc']}</i>\n\n"
        f"Сессия #{state.session_count + 1}  ·  "
        f"Всего фокуса: {state.total_focus_minutes} мин"
    )


def _live_status(state: UserState) -> str:
    label = PHASE_LABELS.get(state.phase, state.phase)
    time_str = fmt_time(state.seconds_remaining)
    bar = progress_bar(state.progress)
    return (
        f"<b>{label}</b>\n\n"
        f"⏱  <code>{time_str}</code>  осталось\n"
        f"{bar}\n\n"
        f"Сессия #{state.session_count + 1}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Image / audio senders
# ──────────────────────────────────────────────────────────────────────────────

async def _send_hourglass(
    bot: Bot,
    chat_id: int,
    state: UserState,
    caption: str = "",
    reply_markup=None,
) -> int | None:
    """Send (or re-send) the hourglass image. Returns message_id."""
    img_buf = create_hourglass_image(
        progress=state.progress,
        phase=state.phase,
        time_str=fmt_time(state.seconds_remaining),
        mode_name=state.mode["name"],
        session_count=state.session_count,
        sessions_before_long=state.sessions_before_long(),
        seed=state.session_count,
    )
    try:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(img_buf, filename="hourglass.png"),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return msg.message_id
    except TelegramError as e:
        logger.warning("Failed to send hourglass photo: %s", e)
        return None


async def _send_sound(bot: Bot, chat_id: int, sound_name: str, title: str):
    """Send a generated sound as a voice message."""
    wav_bytes = get_sound(sound_name)
    try:
        await bot.send_voice(
            chat_id=chat_id,
            voice=InputFile(BytesIO(wav_bytes), filename=f"{sound_name}.wav"),
            caption=title,
        )
    except TelegramError as e:
        logger.warning("Failed to send sound %s: %s", sound_name, e)


# ──────────────────────────────────────────────────────────────────────────────
# Timer callbacks  (called by run_countdown)
# ──────────────────────────────────────────────────────────────────────────────

def _make_tick_callback(bot: Bot, chat_id: int):
    async def on_tick(state: UserState):
        """Edit the status text message every minute."""
        if state.status_message_id is None:
            return
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.status_message_id,
                text=_live_status(state),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_running(),
            )
        except TelegramError as e:
            logger.debug("Tick edit failed: %s", e)
    return on_tick


def _make_finish_callback(bot: Bot, chat_id: int):
    async def on_finish(state: UserState):
        """Handle phase completion."""
        phase = state.phase

        if phase == "focus":
            state.on_focus_complete()
            next_break = state.next_break_type()

            # Delete old status text, send completion image + bell
            await _delete_status(bot, chat_id, state)
            await _send_sound(bot, chat_id, "bell", "✅ Фокус завершён!")
            state.start_phase(next_break)   # set up next phase for image
            state.seconds_remaining = state.total_seconds   # show 100% bottom
            img_buf = create_hourglass_image(
                progress=1.0,
                phase=next_break,
                time_str=fmt_time(state.mode[next_break] * 60),
                mode_name=state.mode["name"],
                session_count=state.session_count,
                sessions_before_long=state.sessions_before_long(),
                seed=state.session_count,
            )
            caption = (
                f"✅ <b>Фокус #{state.session_count} завершён!</b>\n\n"
                f"Всего фокуса: {state.total_focus_minutes} мин\n"
                f"Выбери следующий шаг:"
            )
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(img_buf, filename="hourglass.png"),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_after_focus(next_break),
                )
            except TelegramError as e:
                logger.warning("send_photo on_finish focus: %s", e)

            # Reset state to idle so buttons work fresh
            state.phase = "idle"

        elif phase in ("short_break", "long_break"):
            await _delete_status(bot, chat_id, state)
            await _send_sound(bot, chat_id, "ascending", "⏰ Перерыв окончен!")
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⏰ <b>Перерыв окончен!</b>\n\n"
                        "Готов вернуться к работе? 💪"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_after_break(),
                )
            except TelegramError as e:
                logger.warning("send_message on_finish break: %s", e)
            state.phase = "idle"

    return on_finish


async def _delete_status(bot: Bot, chat_id: int, state: UserState):
    """Try to delete the live-status text message."""
    if state.status_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=state.status_message_id)
        except TelegramError:
            pass
        state.status_message_id = None


# ──────────────────────────────────────────────────────────────────────────────
# Phase launcher
# ──────────────────────────────────────────────────────────────────────────────

async def _launch_phase(bot: Bot, chat_id: int, state: UserState, phase: str):
    """Stop any running timer, configure state, send image+sound, start task."""
    state.cancel_task()
    await _delete_status(bot, chat_id, state)

    state.start_phase(phase)

    # Send sand sound only for focus sessions
    if phase == "focus":
        await _send_sound(bot, chat_id, "sand", "")

    # Hourglass image
    mid = await _send_hourglass(
        bot, chat_id, state,
        caption=f"<b>{PHASE_LABELS[phase]}</b>  ·  {fmt_time(state.total_seconds)}",
        reply_markup=None,
    )
    state.photo_message_id = mid

    # Live-status text (edited by on_tick)
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=_live_status(state),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_running(),
        )
        state.status_message_id = msg.message_id
    except TelegramError as e:
        logger.warning("Could not send status message: %s", e)

    # Spawn countdown task
    on_tick    = _make_tick_callback(bot, chat_id)
    on_finish  = _make_finish_callback(bot, chat_id)
    state._task = asyncio.create_task(
        run_countdown(state, on_tick, on_finish, tick_interval=60)
    )


# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    state.reset()

    await update.message.reply_text(
        "⏳ <b>Pomodoro Focus Timer</b>\n\n"
        "Помогу тебе сосредоточиться с помощью метода Помодоро.\n"
        "Выбери режим и нажми <b>«Начать фокус»</b>.\n\n"
        + _mode_summary(state),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main_menu(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# /stop
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    state.reset()
    await update.message.reply_text(
        "⏹ Таймер остановлен.\n\n" + _mode_summary(state),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main_menu(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# /stats
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    mode = state.mode
    hours, minutes = divmod(state.total_focus_minutes, 60)
    time_str = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"

    await update.message.reply_text(
        "📊 <b>Твоя статистика</b>\n\n"
        f"Режим:           {mode['name']}\n"
        f"Сессий:          {state.session_count}\n"
        f"Фокус итого:    {time_str}\n"
        f"Циклов:          {state.session_count // mode['sessions_before_long']}\n\n"
        "Продолжай в том же духе! 💪",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main_menu(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# /mode
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎛 <b>Выбери режим:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_mode_select(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Inline button dispatcher
# ──────────────────────────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data    = query.data
    chat_id = update.effective_chat.id
    bot     = ctx.bot
    state   = get_state(chat_id)

    # ── Mode selection ────────────────────────────────────────────────────────
    if data.startswith("mode_"):
        mode_key = data[5:]
        if mode_key in MODES:
            state.mode_key = mode_key
            m = MODES[mode_key]
            await query.edit_message_text(
                f"✅ Режим установлен:\n\n"
                f"<b>{m['name']}</b>\n"
                f"<i>{m['desc']}</i>\n\n"
                f"Нажми «Начать фокус» когда будешь готов!",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_main_menu(),
            )
        return

    # ── Mode chooser screen ───────────────────────────────────────────────────
    if data == "choose_mode":
        await query.edit_message_text(
            "🎛 <b>Выбери режим:</b>\n\n"
            "Это изменит длительность фокуса и перерывов.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_mode_select(),
        )
        return

    # ── Back to main menu ─────────────────────────────────────────────────────
    if data == "back_main":
        await query.edit_message_text(
            _mode_summary(state),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main_menu(),
        )
        return

    # ── Stats ─────────────────────────────────────────────────────────────────
    if data == "stats":
        mode = state.mode
        hours, minutes = divmod(state.total_focus_minutes, 60)
        time_str = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
        await query.edit_message_text(
            "📊 <b>Твоя статистика</b>\n\n"
            f"Режим:          {mode['name']}\n"
            f"Сессий:         {state.session_count}\n"
            f"Фокус итого:   {time_str}\n"
            f"Циклов:         {state.session_count // max(1, mode['sessions_before_long'])}\n\n"
            "Продолжай в том же духе! 💪",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main_menu(),
        )
        return

    # ── Stop ──────────────────────────────────────────────────────────────────
    if data == "stop":
        state.reset()
        await query.edit_message_text(
            "⏹ Таймер остановлен.\n\n" + _mode_summary(state),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main_menu(),
        )
        return

    # ── Skip current phase ────────────────────────────────────────────────────
    if data == "skip":
        if not state.is_running:
            await query.answer("Таймер не запущен.", show_alert=True)
            return
        state.cancel_task()
        phase = state.phase

        if phase == "focus":
            state.on_focus_complete()
            next_break = state.next_break_type()
            await query.edit_message_text(
                f"⏭ Фокус пропущен. Начинаем перерыв...",
                parse_mode=ParseMode.HTML,
            )
            await _launch_phase(bot, chat_id, state, next_break)
        else:
            await query.edit_message_text(
                "⏭ Перерыв пропущен. Начинаем новый фокус...",
                parse_mode=ParseMode.HTML,
            )
            await _launch_phase(bot, chat_id, state, "focus")
        return

    # ── Start focus ───────────────────────────────────────────────────────────
    if data == "start_focus":
        await query.edit_message_text(
            "🍅 Запускаю фокус-сессию...",
            parse_mode=ParseMode.HTML,
        )
        await _launch_phase(bot, chat_id, state, "focus")
        return

    # ── Start short / long break ──────────────────────────────────────────────
    if data in ("start_short_break", "start_long_break"):
        phase = "short_break" if data == "start_short_break" else "long_break"
        label = "короткий перерыв" if phase == "short_break" else "длинный перерыв"
        await query.edit_message_text(
            f"☕ Запускаю {label}...",
            parse_mode=ParseMode.HTML,
        )
        await _launch_phase(bot, chat_id, state, phase)
        return


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop",  cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("mode",  cmd_mode))
    app.add_handler(CallbackQueryHandler(on_callback))

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
