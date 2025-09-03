from asyncio import  Queue, Task, Future, Event
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import  Callable, Awaitable, Optional

from phoenix_channels_python_client.phx_messages import PHXEventMessage, ChannelMessage


@unique
class SubscriptionStatus(IntEnum):
    FAILED = 0
    SUCCESS = 1




@dataclass(frozen=True)
class TopicSubscribeResult:
    status: SubscriptionStatus
    result_message: PHXEventMessage


@dataclass()
class TopicSubscription:
    """Represents a topic subscription with all necessary components for message handling"""
    name: str
    async_callback: Callable[[ChannelMessage], Awaitable[None]]
    queue: Queue[ChannelMessage]
    subscription_result: Future[TopicSubscribeResult]
    process_topic_messages_task: Task[None] = None
    # Signaling mechanism for leave requests
    leave_requested: Event = field(default_factory=Event)
    leave_result_future: Optional[Future[TopicSubscribeResult]] = None
    # Current callback task tracking
    current_callback_task: Optional[Task[None]] = None
