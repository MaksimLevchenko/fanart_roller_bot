import os
import random
import asyncio
import aiosqlite
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

DB_PATH = "lists.db"
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------- Storage ----------
class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        logger.info("Initializing DB at %s", self.path)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS words (
                    user_id INTEGER NOT NULL,
                    list_name TEXT NOT NULL,
                    word TEXT NOT NULL,
                    PRIMARY KEY (user_id, list_name, word)
                )
                """
            )
            await db.commit()
        logger.info("DB initialized")

    async def get_words(self, user_id: int, list_name: str) -> list[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT word FROM words WHERE user_id=? AND list_name=? ORDER BY word",
                (user_id, list_name),
            )
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def add_word(self, user_id: int, list_name: str, word: str) -> bool:
        word = word.strip()
        if not word:
            return False
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute(
                    "INSERT INTO words(user_id, list_name, word) VALUES(?,?,?)",
                    (user_id, list_name, word),
                )
                await db.commit()
                logger.info("Added word=%r user=%s list=%s", word, user_id, list_name)
                return True
            except aiosqlite.IntegrityError:
                logger.info("Word exists=%r user=%s list=%s", word, user_id, list_name)
                return False

    async def remove_word(self, user_id: int, list_name: str, word: str) -> bool:
        word = word.strip()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM words WHERE user_id=? AND list_name=? AND word=?",
                (user_id, list_name, word),
            )
            await db.commit()
            removed = cur.rowcount > 0
            if removed:
                logger.info("Removed word=%r for user=%s list=%s", word, user_id, list_name)
            else:
                logger.info("Word not found for removal=%r user=%s list=%s", word, user_id, list_name)
            return removed

    async def clear_list(self, user_id: int, list_name: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM words WHERE user_id=? AND list_name=?",
                (user_id, list_name),
            )
            await db.commit()
        logger.info("Cleared list=%s for user=%s", list_name, user_id)

    async def roll(self, user_id: int):
        a = await self.get_words(user_id, "A")
        b = await self.get_words(user_id, "B")
        if not a or not b:
            return None, None
        return random.choice(a), random.choice(b)


storage = Storage(DB_PATH)


# ---------- UI ----------
def kb_main():
    kb = InlineKeyboardBuilder()
    kb.button(text="Редактировать список Фандомов", callback_data="edit:A")
    kb.button(text="Редактировать список Ситуаций", callback_data="edit:B")
    kb.button(text="Ролл", callback_data="roll")
    kb.adjust(1)
    return kb.as_markup()


def kb_edit(list_name: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="Добавить слово", callback_data=f"add:{list_name}")
    kb.button(text="Удалить слово", callback_data=f"remove:{list_name}")
    kb.button(text="Назад", callback_data="back")
    kb.adjust(1)
    return kb.as_markup()


def kb_cancel():
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена / Назад", callback_data="back")
    kb.adjust(1)
    return kb.as_markup()


def make_remove_kb(list_name: str, words: list[str]) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для удаления: каждая кнопка — слово с callback_data 'do_remove:{list}:{index}'.
    Используем индекс, чтобы не упираться в лимит длины callback_data.
    """
    kb = InlineKeyboardBuilder()
    for i, w in enumerate(words):
        # Текст кнопки — само слово (может быть длинным; UI клиента обрежет)
        kb.button(text=w, callback_data=f"do_remove:{list_name}:{i}")
    kb.button(text="Назад", callback_data=f"remove_back:{list_name}")
    kb.adjust(1)
    return kb.as_markup()


async def text_main(user_id: int):
    a = await storage.get_words(user_id, "A")
    b = await storage.get_words(user_id, "B")
    return (
        "Добро пожаловать!\n\n"
        f"Фандомы: {len(a)} слов\n"
        f"Ситуации: {len(b)} слов\n\n"
        "Выбери действие:"
    )


async def text_edit(user_id: int, list_name: str):
    words = await storage.get_words(user_id, list_name)
    preview = "\n".join(f"• {w}" for w in words[:25]) or "— пусто —"
    tail = "\n…" if len(words) > 25 else ""
    return (
        f"Редактирование списка {list_name}\n\n"
        f"Слова ({len(words)}):\n{preview}{tail}"
    )


# ---------- FSM ----------
class EditStates(StatesGroup):
    waiting_add = State()


router = Router()


# ---------- Handlers ----------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await text_main(message.from_user.id), reply_markup=kb_main())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await text_main(message.from_user.id), reply_markup=kb_main())


@router.callback_query(F.data == "back")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        await text_main(callback.from_user.id),
        reply_markup=kb_main(),
    )


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    list_name = callback.data.split(":", 1)[1]
    await callback.message.edit_text(
        await text_edit(callback.from_user.id, list_name),
        reply_markup=kb_edit(list_name),
    )


@router.callback_query(F.data == "roll")
async def cb_roll(callback: CallbackQuery):
    await callback.answer()
    a, b = await storage.roll(callback.from_user.id)
    if not a or not b:
        await callback.message.edit_text(
            "Один из списков пуст.", reply_markup=kb_main()
        )
        return
    await callback.message.edit_text(
        f"Фандомы: {a}\nСитуации: {b}", reply_markup=kb_main()
    )


@router.callback_query(F.data.startswith("add:"))
async def cb_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    list_name = callback.data.split(":", 1)[1]
    await state.set_state(EditStates.waiting_add)
    await state.update_data(
        list_name=list_name,
        menu_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        f"Добавление в список {list_name}\n\n"
        "Можно написать несколько строк — каждая непустая строка станет отдельным словом.",
        reply_markup=kb_cancel(),
    )


@router.message(EditStates.waiting_add)
async def msg_add(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    list_name = data["list_name"]
    menu_message_id = data["menu_message_id"]

    lines = [line.strip() for line in message.text.splitlines()]
    lines = [line for line in lines if line]

    added = 0
    skipped = 0

    for line in lines:
        ok = await storage.add_word(message.from_user.id, list_name, line)
        if ok:
            added += 1
        else:
            skipped += 1

    logger.info(
        "Bulk add user=%s list=%s added=%d skipped=%d",
        message.from_user.id,
        list_name,
        added,
        skipped,
    )

    try:
        await message.delete()
    except Exception:
        pass

    await state.clear()

    suffix = f"\n\nДобавлено: {added}\nПропущено: {skipped}"
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=menu_message_id,
        text=(await text_edit(message.from_user.id, list_name)) + suffix,
        reply_markup=kb_edit(list_name),
    )


# ---------- Deletion via selection ----------
@router.callback_query(F.data.startswith("remove:"))
async def cb_remove_show_list(callback: CallbackQuery):
    await callback.answer()
    list_name = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    words = await storage.get_words(user_id, list_name)
    if not words:
        await callback.message.edit_text(
            (await text_edit(user_id, list_name)) + "\n\nСписок пуст — нечего удалять.",
            reply_markup=kb_edit(list_name),
        )
        return

    await callback.message.edit_text(
        f"Выберите слово для удаления из списка {list_name}:\n\n(нажмите слово — оно будет удалено)",
        reply_markup=make_remove_kb(list_name, words),
    )


@router.callback_query(F.data.startswith("remove_back:"))
async def cb_remove_back(callback: CallbackQuery):
    await callback.answer()
    list_name = callback.data.split(":", 1)[1]
    await callback.message.edit_text(
        await text_edit(callback.from_user.id, list_name),
        reply_markup=kb_edit(list_name),
    )


@router.callback_query(F.data.startswith("do_remove:"))
async def cb_do_remove(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        logger.warning("do_remove callback malformed: %s", callback.data)
        await callback.message.answer("Ошибка: неверные данные.")
        return

    list_name = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        logger.warning("do_remove index not int: %s", parts[2])
        await callback.message.answer("Ошибка: неверный индекс.")
        return

    user_id = callback.from_user.id
    words = await storage.get_words(user_id, list_name)
    if index < 0 or index >= len(words):
        logger.info("do_remove: invalid index %s for user=%s list=%s (len=%d)", index, user_id, list_name, len(words))
        # Список мог измениться — просто вернём экран редактирования
        await callback.message.edit_text(
            await text_edit(user_id, list_name),
            reply_markup=kb_edit(list_name),
        )
        return

    word = words[index]
    ok = await storage.remove_word(user_id, list_name, word)

    suffix = "\n\nУдалено." if ok else "\n\nНе найдено (возможно уже удалено)."
    await callback.message.edit_text(
        (await text_edit(user_id, list_name)) + suffix,
        reply_markup=kb_edit(list_name),
    )


# ---------- Misc ----------
@router.callback_query(F.data.startswith("clear:"))
async def cb_clear(callback: CallbackQuery):
    await callback.answer()
    list_name = callback.data.split(":", 1)[1]
    await storage.clear_list(callback.from_user.id, list_name)
    await callback.message.edit_text(
        await text_edit(callback.from_user.id, list_name),
        reply_markup=kb_edit(list_name),
    )


async def main():
    if not TOKEN:
        logger.error("BOT_TOKEN is not set")
        raise RuntimeError("BOT_TOKEN missing")

    await storage.init()

    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        try:
            await bot.close()
        except Exception:
            logger.exception("Error closing bot")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("Fatal error")
