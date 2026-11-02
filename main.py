import logging
import asyncio
import os
import uuid

from ocr_ollama import handle_image
from fsm import FSM
from bot import MaxBot

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = MaxBot("f9LHodD0cOJLHOBJ4-9Xt7loUbFPG3TFZfgqw7kOn4J4-xH3rkIt5clprOlDV1M5bGlmtvu1ngvWqSWbxXJE")
fsm = FSM()


START_KB = lambda: bot.build_inline_keyboard(
    [[bot.btn_callback("Создать рейс", "add_reys")]]
)

DONE_KB = lambda: bot.build_inline_keyboard(
    [[bot.btn_callback("Готово ✅", "completed")],
     [bot.btn_callback("Отменить ❌", "cancel")]],
)


def next_num_reys(user_id: int) -> int:
    base = os.path.join("uploads", str(user_id))
    os.makedirs(base, exist_ok=True)
    existing = [d for d in os.listdir(base) if d.isdigit()]
    return max((int(d) for d in existing), default=0) + 1


@bot.on_started
async def on_bot_started(ctx):
    text = (
        f"Добро пожаловать, **{ctx.first_name}**!\n"
        "Я бот для постройки маршрута рейса. "
        "Чтобы создать рейс и начать строить нажми на кнопку ниже:"
    )
    await ctx.send(text, fmt="Markdown", attachments=[START_KB()])


@bot.command("create_reys")
@bot.on_callback("add_reys")
async def on_add_reys(ctx):
    st = fsm.get(ctx.user_id)
    if st is None:
        num_reys = next_num_reys(ctx.user_id)
        fsm.set(ctx.user_id, "waiting_screens", screens=[], num_reys=num_reys)
        await ctx.send(
            "Пришли скрины для распознавания адресов.\n"
            "Когда закончишь — напиши «готово» или нажми кнопку ниже.\n",
            attachments=[DONE_KB()],
        )
    else:
        await ctx.send("Вы уже начали разработку рейса, чтобы отменить напиши `/cancel`", fmt="markdown")


@fsm.state("waiting_screens")
async def waiting_screens(ctx, st):
    text = (ctx.text or "").strip().lower()

    if text == "готово":
        await finish_screens(ctx, st)
        return

    photos = [a for a in ctx.attachments if a.get("type") in ("image", "photo")]
    if photos:
        num_reys = st.data.get("num_reys", 1)
        base_dir = os.path.join("uploads", str(ctx.user_id), str(num_reys))
        os.makedirs(base_dir, exist_ok=True)

        saved = 0
        for photo in photos:
            payload = photo.get("payload") or {}
            url = payload.get("url")
            if not url:
                continue
            photo_id = payload.get("photo_id") or payload.get("token") or "img"
            filename = f"{uuid.uuid4().hex}_{photo_id}.jpg"
            dest = os.path.join(base_dir, filename)
            try:
                await ctx.bot.download_file(url, dest)
                st.data.setdefault("screens", []).append({
                    "photo_id": photo_id,
                    "path": dest,
                    "url": url,
                })
                saved += 1
            except Exception:
                log.exception("Не удалось скачать фото %s", url)

        if saved:
            await ctx.send(
                f"Принято скринов: {len(st.data['screens'])}. Ещё или «готово»?",
                attachments = [DONE_KB()]
            )
        else:
            await ctx.send("Не удалось скачать фото. Попробуй ещё раз.")
        return

    await ctx.send("Пришли скрин или напиши «готово».")

@fsm.state("poryadok")
async def waiting_order(ctx, st):
    text = (ctx.text or "").strip().lower()
    addresses = st.data.get("addresses", [])
    lines = st.data.get("lines", [])
    n = len(lines)

    if text in ("как есть", "как есть.", "as is", "нет"):
        order = list(range(1, n + 1))
    else:
        # вытаскиваем все цифры: "1942" -> [1,9,4,2], "1 9 4 2" -> [1,9,4,2]
        digits = [int(c) for c in text if c.isdigit()]
        if len(digits) != n:
            await ctx.send(
                f"Нужно ввести ровно {n} цифр — по одной на каждый адрес.\n"
                f"У тебя адресов: {n}. Попробуй ещё раз или напиши «как есть»."
            )
            return
        if sorted(digits) != list(range(1, n + 1)):
            await ctx.send(
                f"Цифры должны быть от 1 до {n} без повторов.\n"
                f"Например: `{''.join(str(i) for i in range(1, n + 1))}`"
            )
            return
        order = digits

    ordered = [lines[i - 1] for i in order]
    ordered_addresses = [addresses[i - 1] for i in order]

    num_reys = st.data.get("num_reys", 1)
    route_dir = os.path.join("uploads", str(ctx.user_id), str(num_reys))
    os.makedirs(route_dir, exist_ok=True)
    route_path = os.path.join(route_dir, "route.txt")
    with open(route_path, "w", encoding="utf-8") as f:
        for i, (line, addr) in enumerate(zip(ordered, ordered_addresses), 1):
            f.write(f"{i}. {addr}\n")

    fsm.clear(ctx.user_id)

    result = "✅ Маршрут построен:\n\n" + "\n".join(
        f"{i}. {line}" for i, line in enumerate(ordered, 1)
    )
    result += "\n\nЧтобы создать новый рейс — /create_reys"
    await ctx.send(result, fmt="markdown")    


@bot.on_callback("completed")
async def on_completed(ctx):
    st = fsm.get(ctx.user_id)
    if st is None or st.name != "waiting_screens":
        await ctx.send("Нечего завершать. Нажми:", attachments=[START_KB()])
        return
    await finish_screens(ctx, st)


async def finish_screens(ctx, st):
    screens = st.data.get("screens", [])
    num_reys = st.data.get("num_reys", 1)

    if not screens:
        fsm.clear(ctx.user_id)
        await ctx.send("Ты не прислал ни одного скрина. Начни заново: /start")
        return

    resp = await ctx.send(
        f"Готово! Принято скринов: {len(screens)}.\n"
        f"Начинается распознавание 🚀"
    )
    mid = ((resp.get("message") or {}).get("body") or {}).get("mid")

    lines: list[str] = []
    addresses: list[str] = []   # только адреса — для порядка маршрута
    errors: list[str] = []

    for idx, screen in enumerate(screens, 1):
        path = screen.get("path")
        if not path:
            errors.append(f"скрин {idx}: нет пути к файлу")
            continue

        try:
            orders = await handle_image(path)
        except Exception:
            log.exception("Ошибка распознавания %s", path)
            errors.append(f"скрин {idx}: ошибка распознавания")
            continue

        order_number = orders.get("order_number") or "—"
        phone = orders.get("phone") or "—"
        address = orders.get("address") or "—"
        line = f"**{order_number}** — {phone} — {address}"
        lines.append(line)
        addresses.append(address)

        progress = "Распознано 🚀:\n" + "\n".join(lines)
        if mid:
            try:
                await bot.edit_message(mid, progress, fmt="markdown")
            except Exception:
                log.exception("Не удалось обновить прогресс")

    if not lines:
        fsm.clear(ctx.user_id)
        await ctx.send("Не удалось распознать ни одного адреса. Попробуй ещё раз.")
        return

    fsm.set(
        ctx.user_id,
        "poryadok",
        num_reys=num_reys,
        lines=lines,
        addresses=addresses,
        errors=errors,
    )

    result = "**🚚Ваши адреса:**\n\n" + "\n".join(
        f"{i}. {line}" for i, line in enumerate(lines, 1)
    )
    if errors:
        result += "\n\n⚠️ Проблемы:\n" + "\n".join(errors)

    result += (
        "\n\nА теперь давай определим порядок маршрута.\n"
        f"Введи последовательность номеров (например: `{''.join(str(i) for i in range(1, len(lines) + 1))}`)\n"
        "Или напиши «как есть», чтобы оставить текущий порядок."
    )
    await ctx.send(result, fmt="markdown")


@bot.on_callback("cancel")
@bot.command("cancel")
async def cancel(ctx):
    fsm.clear(ctx.user_id)
    await ctx.send("Отменено. /create_reys — начать заново.")


@bot.on_message
async def on_message(ctx):
    if await fsm.handle(ctx):
        return
    await ctx.send("Нажмите /create_reys, чтобы начать.")


async def main():
    try:
        await bot.set_commands([
            {"name": "start",       "description": "Запустить бота и создать рейс"},
            {"name": "create_reys", "description": "Создать рейс"},
            {"name": "cancel",      "description": "Отменить текущее действие"},
        ])
        await bot.run()
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено пользователем")