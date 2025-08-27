import asyncio
from dataclasses import dataclass
from enum import Enum, unique
from functools import cached_property
from typing import Any, NewType, Optional, Protocol, TYPE_CHECKING, Union


if TYPE_CHECKING:
    from phoenix_channels_python_client.client import PHXChannelsClient


Event = NewType('Event', str)
ChannelEvent = Union['PHXEvent', Event]
ChannelMessage = Union['PHXMessage', 'PHXEventMessage']


class ExecutorHandler(Protocol):
    """Protocol describing a handler that will be called using the provided executor pool"""

    def __call__(self, __message: Union['PHXMessage', 'PHXEventMessage'], __client: 'PHXChannelsClient') -> None:
        """
        Args:
            __message (Union[PHXMessage, PHXEventMessage]): The message the handler should process
            __client (PHXChannelsClient): The client instance
        """
        ...  # pragma: no cover


class CoroutineHandler(Protocol):
    """Protocol describing a handler that will be run as a task in the event loop"""

    async def __call__(self, __message: Union['PHXMessage', 'PHXEventMessage'], __client: 'PHXChannelsClient') -> None:
        """
        Args:
            __message (Union[PHXMessage, PHXEventMessage]): The message the handler should process
            __client (PHXChannelsClient): The client instance
        """
        ...  # pragma: no cover


ChannelHandlerFunction = Union[ExecutorHandler, CoroutineHandler]


@unique
class PHXEvent(Enum):
    """Phoenix Channels admin events"""
    close = 'phx_close'
    error = 'phx_error'
    join = 'phx_join'
    reply = 'phx_reply'
    leave = 'phx_leave'

    # hack for typing
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BasePHXMessage:
    topic: str
    ref: Optional[str]
    payload: dict[str, Any]

    @cached_property
    def subtopic(self) -> Optional[str]:
        if ':' not in self.topic:
            return None

        _, subtopic = self.topic.split(':', 1)
        return subtopic


@dataclass(frozen=True)
class PHXMessage(BasePHXMessage):
    event: Event


@dataclass(frozen=True)
class PHXEventMessage(BasePHXMessage):
    event: PHXEvent
