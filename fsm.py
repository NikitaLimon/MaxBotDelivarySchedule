from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional


@dataclass
class UserState:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


class FSM:
    def __init__(self):
        self._states: dict[int, UserState] = {}
        self._handlers: dict[str, Callable] = {}

    def set(self, user_id: int, name: str, **initial):
        self._states[user_id] = UserState(name=name, data=dict(initial))

    def get(self, user_id: int) -> Optional[UserState]:
        return self._states.get(user_id)

    def clear(self, user_id: int):
        self._states.pop(user_id, None)

    def state(self, name: str):
        def decorator(func):
            self._handlers[name] = func
            return func
        return decorator

    async def handle(self, ctx) -> bool:
        st = self.get(ctx.user_id)
        if st is None:
            return False
        handler = self._handlers.get(st.name)
        if handler is None:
            return False
        await handler(ctx, st)
        return True