import asyncio
from asyncio import Event, Queue, Task, Future
from dataclasses import dataclass
from enum import IntEnum, unique
from typing import Optional, Callable

from phoenix_channels_python_client.phx_messages import PHXEventMessage, ChannelMessage, Topic


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
    name: Topic
    callback: Callable[[ChannelMessage], None]
    queue: Queue[ChannelMessage]
    subscription_result: Future[TopicSubscribeResult]
    process_topic_messages_task: Task[None] = None
