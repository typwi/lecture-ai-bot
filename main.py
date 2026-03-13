import os
import asyncio
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from groq import Groq

# Импортируем конфиги
try:
    from config import TELEGRAM_TOKEN, GROQ_API_KEY
except ImportError:
    print("Создайте файл config.py с токенами!")
    exit()

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = Groq(api_key=GROQ_API_KEY)

# Хранилище истории: {user_id: [messages]}
storage = {}
MAX_HISTORY_MESSAGES = 20

# Хранилище последней расшифровки ГС для команды /ras
last_transcription = {}

# ОБНОВЛЕННЫЙ ПРОМПТ: требуем точную цитату и краткий ответ
SYSTEM_PROMPT = (
    "Ты — помощник-конспектер лекций. Твоя задача — запоминать данные и отвечать на вопросы.\n"
    "1. Если сообщение пользователя — это просто лекция или информация (без явного вопроса), "
    "ответь строго одним словом: NO_QUESTION\n"
    "2. Если пользователь задает вопрос, ответь строго в таком формате:\n"
    "[Точная цитата вопроса пользователя, слово в слово]\n\n"
    "❗️ [Твой ответ: максимально кратко, по делу, только факты, без долгих вступлений]"
)


async def transcribe_voice(file_id: str):
    file = await bot.get_file(file_id)
    file_buffer = io.BytesIO()
    await bot.download_file(file.file_path, destination=file_buffer)
    file_buffer.name = "audio.ogg"

    transcription = client.audio.transcriptions.create(
        file=("audio.ogg", file_buffer.getvalue()),
        model="whisper-large-v3",
    )
    return transcription.text


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Присылай информацию (текст или ГС). Когда я увижу вопрос — я отвечу.")


@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    storage[message.from_user.id] = []
    await message.answer("Контекст очищен. Начинаем с чистого листа.")


@dp.message(Command("ras"))
async def cmd_ras(message: types.Message):
    """Показывает расшифровку последнего отправленного ГС"""
    user_id = message.from_user.id
    text = last_transcription.get(user_id)
    if text:
        await message.answer(f"📝 *Расшифровка:*\n{text}", parse_mode="Markdown")
    else:
        await message.answer("Нет сохраненной расшифровки для показа.")


@dp.message(F.voice)
async def handle_voice(message: types.Message):
    msg = await message.answer("Слушаю...")
    user_id = message.from_user.id

    try:
        # Транскрибируем и сохраняем для /ras
        text = await transcribe_voice(message.voice.file_id)
        last_transcription[user_id] = text

        # Работа с памятью
        if user_id not in storage: storage[user_id] = []
        storage[user_id].append({"role": "user", "content": text})

        if len(storage[user_id]) > MAX_HISTORY_MESSAGES:
            storage[user_id] = storage[user_id][-MAX_HISTORY_MESSAGES:]

        # Запрос к LLM
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + storage[user_id]
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
        )
        response = completion.choices[0].message.content

        storage[user_id].append({"role": "assistant", "content": response})

        # Логика выдачи ответа
        if "NO_QUESTION" in response:
            await msg.edit_text("Показать расшифровку - /ras")
        else:
            await msg.edit_text(f"Показать расшифровку - /ras\n\n{response}")

    except Exception as e:
        await msg.edit_text("Ошибка при обработке голоса.")
        print(f"Ошибка: {e}")


@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text.startswith('/'): return

    user_id = message.from_user.id
    if user_id not in storage: storage[user_id] = []

    storage[user_id].append({"role": "user", "content": message.text})
    if len(storage[user_id]) > MAX_HISTORY_MESSAGES:
        storage[user_id] = storage[user_id][-MAX_HISTORY_MESSAGES:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + storage[user_id]

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
        )
        response = completion.choices[0].message.content
        storage[user_id].append({"role": "assistant", "content": response})

        # Для текста команда /ras не нужна
        if "NO_QUESTION" in response:
            await message.answer("Принято.")
        else:
            await message.answer(response)

    except Exception as e:
        await message.answer("Ошибка при обработке текста.")
        print(e)


async def main():
    print("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())