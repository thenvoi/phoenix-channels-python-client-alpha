from asyncio import  Queue, Task, Future, Event
from dataclasses import dataclass, field
from enum import Enum
from typing import  Callable, Awaitable, Optional

from phoenix_channels_python_client.phx_messages import ChannelMessage


class TopicProcessingState(Enum):
    WAITING_FOR_JOIN = "waiting_for_join"
    PROCESSING_LEAVE = "processing_leave"
    NORMAL_PROCESSING = "normal_processing"


@dataclass()
class TopicSubscription:
    """Represents a topic subscription with all necessary components for message handling"""
    name: str
    async_callback: Callable[[ChannelMessage], Awaitable[None]]
    queue: Queue[ChannelMessage]
    # Single future that completes when subscription is established or fails with exception
    subscription_ready: Future[None]
    # Unique reference for this subscription
    join_ref: str
    process_topic_messages_task: Task[None] = None
    # Signaling mechanism for leave requests
    leave_requested: Event = field(default_factory=Event)
    # Future that completes when unsubscribe is done
    unsubscribe_completed: Optional[Future[None]] = None
    # Current callback task tracking
    current_callback_task: Optional[Task[None]] = None
