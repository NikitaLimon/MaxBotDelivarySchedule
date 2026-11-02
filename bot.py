import asyncio
import inspect
import logging
import os
import ssl
from typing import Any, Awaitable, Callable, Optional
import certifi
import aiohttp

logger = logging.getLogger(__name__)


class Context:
    def __init__(self, bot: "MaxBot", update: dict) -> None:
        self.bot = bot
        self.update = update
        self.update_type = update.get("update_type")

        message = update.get("message") or {}
        self.message = message
        self.body = message.get("body") or {}

        # mid исходного сообщения — нужен для reply
        self.mid: Optional[str] = self.body.get("mid")

        self.text: Optional[str] = self.body.get("text")
        self.command: Optional[str] = None
        self.args: str = ""

        # --- кто инициатор ---
        # Для message_callback автора нажатия надо брать из update.callback.user,
        # а message.sender — это бот, отправивший сообщение с кнопкой.
        callback = update.get("callback") or {}
        if callback.get("user"):
            self.sender = callback["user"]
        else:
            self.sender = message.get("sender") or update.get("user") or {}

        self.recipient = message.get("recipient") or {}
        if not self.recipient and update.get("chat_id") is not None:
            self.recipient = {"chat_id": update.get("chat_id")}

        # callback
        self.callback = callback
        self.callback_payload = self.callback.get("payload")
        if isinstance(self.callback_payload, dict):
            self.callback_payload = self.callback_payload.get("payload")

        # id-шники
        self.chat_id: Optional[int] = (
            self.recipient.get("chat_id") or update.get("chat_id")
        )
        self.user_id: Optional[int] = (
            self.sender.get("user_id") or update.get("user_id")
        )
        self.chat_type: Optional[str] = (
            self.recipient.get("chat_type") or update.get("chat_type")
        )

        # --- данные пользователя ---
        self.first_name: Optional[str] = self.sender.get("first_name")
        self.last_name: Optional[str] = self.sender.get("last_name")
        self.username: Optional[str] = self.sender.get("username")
        self.is_bot: Optional[bool] = self.sender.get("is_bot")
        self.last_activity_time: Optional[int] = self.sender.get("last_activity_time")

    # --- прокси к update ---
    def get(self, key: str, default: Any = None) -> Any:
        return self.update.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.update[key]

    def __contains__(self, key: str) -> bool:
        return key in self.update

    def keys(self):
        return self.update.keys()

    def items(self):
        return self.update.items()

    def values(self):
        return self.update.values()

    # --- ответы ---
    async def reply(
        self,
        text: str,
        *,
        reply_to: str | None = None,
        forward_from: str | None = None,
        fmt: str | None = None,
        attachments: list | None = None,
        **kwargs,
    ) -> dict:
        """Ответ на сообщение (link.type = reply) по умолчанию.

        reply_to — mid сообщения, на которое отвечаем.
        По умолчанию — mid текущего апдейта (если есть).
        forward_from — mid сообщения для пересылки (link.type = forward).
        """
        link = None
        if forward_from is not None:
            link = {"type": "forward", "mid": forward_from}
        else:
            mid = reply_to if reply_to is not None else self.mid
            if mid is not None:
                link = {"type": "reply", "mid": mid}

        return await self.bot.send_message(
            text,
            chat_id=self.chat_id,
            user_id=None if self.chat_id else self.user_id,
            fmt=fmt,
            link=link,
            attachments=attachments,
            **kwargs,
        )

    async def send(self, text: str, **kwargs) -> dict:
        return await self.bot.send_message(
            text,
            chat_id=self.chat_id,
            user_id=None if self.chat_id else self.user_id,
            **kwargs,
        )

    async def answer_callback(self, text: str, **kwargs) -> dict:
        return await self.send(text, **kwargs)

    def __repr__(self) -> str:
        return (
            f"<Context type={self.update_type!r} mid={self.mid!r} "
            f"user_id={self.user_id} chat_id={self.chat_id} "
            f"chat_type={self.chat_type!r} text={self.text!r}>"
        )

    @property
    def attachments(self) -> list[dict]:
        """Все вложения: обычные + из пересланного сообщения (link.type = forward)."""
        result = list(self.body.get("attachments") or [])

        # вложения могут лежать и на верхнем уровне message
        result.extend(self.message.get("attachments") or [])

        link = self.message.get("link") or {}
        if link.get("type") == "forward":
            fwd_msg = link.get("message") or {}
            # вложения могут лежать и в message.attachments, и в message.body.attachments
            result.extend(fwd_msg.get("attachments") or [])
            fwd_body = fwd_msg.get("body") or {}
            result.extend(fwd_body.get("attachments") or [])

        return result

class MaxBot:

    BASE_URL = "https://platform-api2.max.ru"

    def __init__(
        self,
        token: str,
        *,
        ca_file: Optional[str] = "sert.pem",
        long_polling_timeout: int = 90,
    ) -> None:
        self.token = token
        self._ca_file = ca_file
        self._long_polling_timeout = long_polling_timeout

        self._session: Optional[aiohttp.ClientSession] = None
        self._marker: Optional[int] = None
        self._running = False

        self._message_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._started_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._callback_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._callback_handlers: dict[str, Callable[..., Awaitable[None]]] = {}
        self._command_handlers: dict[str, Callable[..., Awaitable[None]]] = {}

    # ---------- HTTP ----------
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=100)
            timeout = aiohttp.ClientTimeout(total=self._long_polling_timeout + 10)
            self._session = aiohttp.ClientSession(
                base_url=self.BASE_URL,
                headers={"Authorization": self.token},
                connector=connector,
                timeout=timeout,
            )
        return self._session

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if self._ca_file:
            return ssl.create_default_context(cafile=self._ca_file)
        return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(self, method, path, *, params=None, json=None) -> dict:
        session = await self._get_session()
        async with session.request(
            method, path, params=params, json=json, ssl=self._ssl_context()
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                logger.error(
                    "Max API %s %s -> %s\nparams=%s\njson=%s\nresponse=%s",
                    method, path, resp.status, params, json, body,
                )
            resp.raise_for_status()
            return await resp.json(content_type=None) if body else {}

    async def download_file(self, url: str, dest_path: str) -> str:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        session = await self._get_session()
        ctx = ssl.create_default_context(cafile=certifi.where())
        async with session.get(url, ssl=ctx, allow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)
        return dest_path

    async def get_updates(self, *, marker: Optional[int] = None) -> dict:
        params = {"timeout": self._long_polling_timeout, "limit": 100}
        if marker is not None:
            params["marker"] = marker
        return await self.request("GET", "/updates", params=params)

    async def send_message(
            self,
            text: str,
            *,
            chat_id: int | None = None,
            user_id: int | None = None,
            attachments: list | None = None,
            fmt: str | None = None,
            link: dict | None = None,
            notify: bool | None = None,
            disable_link_preview: bool | None = None,
        ) -> dict:
            if chat_id is None and user_id is None:
                raise ValueError("Нужен chat_id или user_id")

            params: dict = {}
            if chat_id is not None:
                params["chat_id"] = chat_id
            if user_id is not None:
                params["user_id"] = user_id
            if disable_link_preview is not None:
                params["disable_link_preview"] = disable_link_preview

            payload: dict = {"text": text}
            if fmt:
                payload["format"] = fmt
            if attachments:
                if isinstance(attachments, dict):
                    payload["attachments"] = [attachments]
                else:
                    payload["attachments"] = list(attachments)
            if link is not None:
                payload["link"] = link
            if notify is not None:
                payload["notify"] = notify

            return await self.request("POST", "/messages", params=params, json=payload)
    async def edit_message(
        self,
        message_id: str,
        text: str | None = None,
        *,
        attachments: list | None = None,
        fmt: str | None = None,
        link: dict | None = None,
        notify: bool | None = None,
    ) -> dict:
        """PUT /messages?message_id=... — редактировать сообщение, отправленное ботом.

        message_id — id сообщения (можно брать из ответа send_message: data["message"]["body"]["mid"]).
        text — до 4000 символов, может быть None (тогда только вложения/кнопки).
        attachments:
        - None  → не трогать вложения (изменений не будет)
        - []    → удалить все вложения
        - [...] → заменить на новые
        """
        if message_id is None:
            raise ValueError("Нужен message_id")

        params = {"message_id": message_id}

        payload: dict = {}
        if text is not None:
            payload["text"] = text
        if fmt:
            payload["format"] = fmt
        if attachments is not None:
            if isinstance(attachments, dict):
                payload["attachments"] = [attachments]
            else:
                payload["attachments"] = list(attachments)
        if link is not None:
            payload["link"] = link
        if notify is not None:
            payload["notify"] = notify

        return await self.request("PUT", "/messages", params=params, json=payload)
    # ---------- клавиатуры ----------
    @staticmethod
    def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
        return {"type": "inline_keyboard", "payload": {"buttons": buttons}}

    @staticmethod
    def btn_link(text: str, url: str) -> dict:
        return {"type": "link", "text": text, "url": url}

    @staticmethod
    def btn_callback(text: str, payload: str) -> dict:
        return {"type": "callback", "text": text, "payload": payload}

    @staticmethod
    def btn_request_contact(text: str) -> dict:
        return {"type": "request_contact", "text": text}

    @staticmethod
    def btn_request_geo_location(text: str) -> dict:
        return {"type": "request_geo_location", "text": text}

    @staticmethod
    def extract_recipient(update: dict) -> tuple[int | None, int | None, str | None]:
        if isinstance(update, Context):
            return update.chat_id, update.user_id, update.chat_type
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        sender = message.get("sender") or {}

        chat_id = recipient.get("chat_id") or update.get("chat_id")
        user_id = sender.get("user_id") or update.get("user_id")
        chat_type = recipient.get("chat_type") or update.get("chat_type")
        return chat_id, user_id, chat_type

    # ---------- регистрация хендлеров ----------
    def on_message(self, func):
        self._message_handler = func
        return func

    def on_started(self, func):
        self._started_handler = func
        return func

    def on_callback(self, name: str | None = None):
        def decorator(func):
            if name is None:
                self._callback_handler = func
            else:
                self._callback_handlers[name] = func
            return func
        if callable(name):
            func, name = name, None
            return decorator(func)
        return decorator

    def command(self, name: str):
        name = name.lstrip("/").lower()

        def decorator(func):
            self._command_handlers[name] = func
            return func
        return decorator

    # ---------- парсинг команды ----------
    @staticmethod
    def _parse_command(text: str) -> tuple[str | None, str]:
        if not text or not text.startswith("/"):
            return None, ""
        first, _, rest = text.partition(" ")
        cmd = first[1:]  
        cmd = cmd.split("@", 1)[0].lower()
        return cmd, rest.strip()
    
    

    # ---------- вызов хендлера ----------
    async def _call_handler(self, handler, ctx: Context, label: str) -> None:
        try:
            sig = inspect.signature(handler)
            params = [
                p for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if len(params) >= 2:
                await handler(self, ctx)
            else:
                await handler(ctx)
        except Exception:
            logger.exception("Ошибка в обработчике %s", label)

    # ---------- диспетчеризация ----------
    async def _handle_update(self, update: dict) -> None:
        ctx = Context(self, update)
        update_type = ctx.update_type

        if update_type == "message_created":
            command, args = self._parse_command(ctx.text or "")
            ctx.command = command
            ctx.args = args

            if command is not None:
                handler = self._command_handlers.get(command)
                if handler is not None:
                    await self._call_handler(handler, ctx, f"command:{command}")
                    return
                if command == "start" and self._started_handler is not None:
                    await self._call_handler(self._started_handler, ctx, "bot_started")
                    return
                if self._message_handler is not None:
                    await self._call_handler(self._message_handler, ctx, "message")
                return

            if self._message_handler is not None:
                await self._call_handler(self._message_handler, ctx, "message")

        elif update_type == "bot_started":
            if self._started_handler is not None:
                await self._call_handler(self._started_handler, ctx, "bot_started")

        elif update_type == "message_callback":
            payload = ctx.callback_payload
            logger.info("CALLBACK payload=%r", payload)
            handler = self._callback_handlers.get(payload) or self._callback_handler
            if handler is None:
                logger.warning("Нет обработчика для callback payload=%r", payload)
                return
            await self._call_handler(handler, ctx, f"callback:{payload}")

        else:
            logger.debug("Необработанный update_type: %s", update_type)

    async def run(self) -> None:
        self._running = True
        logger.info("Бот запущен")
        try:
            while self._running:
                try:
                    data = await self.get_updates(marker=self._marker)
                except asyncio.TimeoutError:
                    continue
                except aiohttp.ClientError as e:
                    logger.warning("HTTP ошибка: %s, переподключение через 5с", e)
                    await asyncio.sleep(5)
                    continue
                except Exception:
                    logger.exception("Неожиданная ошибка, переподключение через 5с")
                    await asyncio.sleep(5)
                    continue

                marker = data.get("marker")
                if marker is not None:
                    self._marker = marker

                for update in data.get("updates", []):
                    asyncio.create_task(self._handle_update(update))
        finally:
            await self.close()
            logger.info("Бот остановлен")
    async def set_commands(self, commands: list[dict]) -> dict:
        """PATCH /me/commands — задать список команд бота для подсказок при вводе «/».

        commands: [{"name": "start", "description": "Запустить бота"}, ...]
        Максимум 32 элемента; name 1–64 символа, description 1–128 (или None).
        Пустой список — удалить все команды.
        """
        return await self.request("PATCH", "/me/commands", json={"commands": commands})
    def stop(self) -> None:
        self._running = False