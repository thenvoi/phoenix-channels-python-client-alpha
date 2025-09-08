from asyncio import  Queue, Task, Future, Event
from dataclasses import dataclass, field
from enum import Enum
from typing import  Callable, Awaitable, Optional, Dict

from phoenix_channels_python_client.phx_messages import ChannelMessage, ChannelEvent


class TopicProcessingState(Enum):
    WAITING_FOR_JOIN = "waiting_for_join"
    PROCESSING_LEAVE = "processing_leave"
    NORMAL_PROCESSING = "normal_processing"


@dataclass()
class TopicSubscription:
    """Represents a topic subscription with all necessary components for message handling"""
    name: str
    async_callback: Optional[Callable[[ChannelMessage], Awaitable[None]]]
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
    # Event-specific handlers mapping
    event_handlers: Dict[ChannelEvent, Callable[[ChannelMessage], Awaitable[None]]] = field(default_factory=dict)
    
    def add_event_handler(self, event: ChannelEvent, handler: Callable[[ChannelMessage], Awaitable[None]]) -> None:
        """Add or update an event handler for a specific event type."""
        self.event_handlers[event] = handler
    
    def remove_event_handler(self, event: ChannelEvent) -> None:
        """Remove an event handler for a specific event type."""
        self.event_handlers.pop(event, None)
    
    def get_event_handler(self, event: ChannelEvent) -> Optional[Callable[[ChannelMessage], Awaitable[None]]]:
        """Get the handler for a specific event type."""
        return self.event_handlers.get(event)
    
    def has_event_handler(self, event: ChannelEvent) -> bool:
        """Check if a handler exists for a specific event type."""
        return event in self.event_handlers
