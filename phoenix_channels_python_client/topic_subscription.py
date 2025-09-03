from asyncio import  Queue, Task, Future, Event
from dataclasses import dataclass, field
from typing import  Callable, Awaitable, Optional

from phoenix_channels_python_client.phx_messages import ChannelMessage


@dataclass()
class TopicSubscription:
    """Represents a topic subscription with all necessary components for message handling"""
    name: str
    async_callback: Callable[[ChannelMessage], Awaitable[None]]
    queue: Queue[ChannelMessage]
    # Single future that completes when subscription is established or fails with exception
    subscription_ready: Future[None]
    process_topic_messages_task: Task[None] = None
    # Signaling mechanism for leave requests
    leave_requested: Event = field(default_factory=Event)
    # Future that completes when unsubscribe is done
    unsubscribe_completed: Optional[Future[None]] = None
    # Current callback task tracking
    current_callback_task: Optional[Task[None]] = None
