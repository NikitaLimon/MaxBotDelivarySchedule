import asyncio
import base64
import json
import re
from pathlib import Path

import ollama

# Модель мультимодальная, поддерживает изображения и русский язык.
# Перед первым запуском: ollama pull qwen2.5vl:3b
MODEL_NAME = "qwen2.5vl:3b"

PROMPT = """Ты — OCR-ассистент. На изображении — карточка заказа интернет-магазина.
Извлеки ровно три поля:

1. order_number — номер заказа (например, H42974601153 или иной буквенно-цифровой код)
2. phone — номер телефона клиента (10 или 11 цифр, можно с +7 или 8)
3. address — полный адрес доставки (регион, район, населенный пункт, улица, дом, квартира)

Правила:
- Верни ТОЛЬКО валидный JSON без markdown, без комментариев, без пояснений.
- Ключи строго: "order_number", "phone", "address".
- Если поле не найдено — значение null.
- Номера телефонов возвращай только цифрами (без пробелов, скобок, дефисов).
- Адрес — одной строкой, как написано в документе.

Пример ответа:
{"order_number": "H42974601153", "phone": "79103331525", "address": "пос. Мичуринский, Брянский район, Брянская область, микрорайон Мегаполис-Парк, 21 кв, кв. 53"}
"""


def _encode_image(path: str | Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _strip_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    return digits or None


def parse_image(path: str | Path) -> dict:
   
    image_b64 = _encode_image(path)

    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": PROMPT,
                "images": [image_b64],
            }
        ],
        options={
            "temperature": 0,      
            "num_predict": 512,   
        },
    )

    raw = response["message"]["content"]
    cleaned = _strip_json(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"Модель вернула не-JSON ответ: {raw!r}")
        data = json.loads(match.group(0))

    return {
        "order_number": data.get("order_number") or None,
        "phone": "+" + _normalize_phone(data.get("phone")),
        "address": data.get("address") or None,
    }


async def handle_image(path: str) -> dict:
    return await asyncio.to_thread(parse_image, path)



    
    
