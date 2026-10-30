import logging
import asyncio
from bot import MaxBot

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = MaxBot("f9LHodD0cOJLHOBJ4-9Xt7loUbFPG3TFZfgqw7kOn4J4-xH3rkIt5clprOlDV1M5bGlmtvu1ngvWqSWbxXJE")

@bot.command("start")
async def start_bot(ctx):
    
    text = f"""Добро пожаловать, **{ctx.first_name}**!
    Я  бот для постройки маршута рейса. Чтобы создать рейс и начать строить нажми на кнопку ниже:"""
    await ctx.reply(text, fmt = "Markdown")
    
    
async def main():
    try:
        await bot.run()
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено пользователем")