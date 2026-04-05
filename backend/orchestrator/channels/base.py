from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class ChannelMessage:
    """Unified message format across all channels."""
    id: str
    user_id: str
    channel: str          # "web", "telegram", "whatsapp"
    text: str
    attachments: list[dict] = field(default_factory=list)  # [{type, data, filename}]
    timestamp: float = 0.0

    @staticmethod
    def new(
        user_id: str,
        channel: str,
        text: str,
        attachments: list[dict] | None = None,
    ) -> "ChannelMessage":
        return ChannelMessage(
            id=str(uuid.uuid4()),
            user_id=user_id,
            channel=channel,
            text=text,
            attachments=attachments or [],
            timestamp=time.time(),
        )


class ChannelAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def send(self, user_id: str, text: str) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
