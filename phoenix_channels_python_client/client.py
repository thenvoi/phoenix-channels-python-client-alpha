import asyncio
import logging
from asyncio import AbstractEventLoop, Queue
from enum import Enum
from logging import Logger
from types import TracebackType
from typing import  Callable, Optional, Type, Union, Awaitable
from websockets import ClientConnection

from websockets import connect

from phoenix_channels_python_client import json_handler
from phoenix_channels_python_client.exceptions import PHXConnectionError, PHXTopicError
from phoenix_channels_python_client.phx_messages import (
    ChannelMessage,
    PHXEvent,
)
from phoenix_channels_python_client.topic_subscription import TopicSubscription
from phoenix_channels_python_client.utils import make_message


class TopicProcessingState(Enum):
    """States for topic message processing"""
    WAITING_FOR_JOIN = "waiting_for_join"
    PROCESSING_LEAVE = "processing_leave"
    NORMAL_PROCESSING = "normal_processing"


class PHXChannelsClient:
    def __init__(
        self,
        channel_socket_url: str,
        event_loop: Optional[AbstractEventLoop] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.channel_socket_url = channel_socket_url
        self.connection = None
        self._topic_subscriptions: dict[str, TopicSubscription] = {}
        self._loop = event_loop or asyncio.get_event_loop()
        self._message_routing_task=None

    async def __aenter__(self) -> 'PHXChannelsClient':
        self.logger.debug('Entering PHXChannelsClient context')
        try:
            self.connection = await connect(self.channel_socket_url)
            self.logger.debug('Successfully connected to Phoenix WebSocket server')
            self._message_routing_task = self._loop.create_task(self._start_processing())
            return self
        except Exception as e:
            self.logger.error(f'Failed to connect to Phoenix WebSocket server: {e}')
            raise PHXConnectionError(f'Failed to connect to {self.channel_socket_url}: {e}') from e

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]] = None,
        exc_value: Optional[BaseException] = None,
        traceback: Optional[TracebackType] = None,
    ) -> None:
        self.logger.debug('Leaving PHXChannelsClient context')
        await self.shutdown('Leaving PHXChannelsClient context')

    async def _send_message(self, websocket: ClientConnection, message: ChannelMessage) -> None:
        self.logger.debug(f'Serialising {message=} to JSON')
        json_message = json_handler.dumps(message)

        self.logger.debug(f'Sending {json_message=}')
        await websocket.send(json_message)

    def _parse_message(self, socket_message: Union[str, bytes]) -> ChannelMessage:
        self.logger.debug(f'Got message - {socket_message=}')
        message_dict = json_handler.loads(socket_message)

        self.logger.debug(f'Decoding message dict - {message_dict=}')
        return make_message(**message_dict)

    async def shutdown(
        self,
        reason: str,
    ) -> None:
        self.logger.info(f'Event loop shutting down! {reason=}')
        await self.connection.close()

        for topic, subscription in self._topic_subscriptions.items():
            if subscription.process_topic_messages_task:
                subscription.process_topic_messages_task.cancel()
                self.logger.debug(f'Cancelled topic subscription task for {topic}')
        
        self._topic_subscriptions.clear()


    def _set_subscription_ready(self, topic_subscription: TopicSubscription) -> None:
        """Safely mark subscription as ready only if not already done"""
        if not topic_subscription.subscription_ready.done():
            topic_subscription.subscription_ready.set_result(None)

    def _set_subscription_error(self, topic_subscription: TopicSubscription, error: Exception) -> None:
        """Safely set subscription error only if not already done"""
        if not topic_subscription.subscription_ready.done():
            topic_subscription.subscription_ready.set_exception(error)

    def _determine_processing_state(self, topic: TopicSubscription) -> TopicProcessingState:
        """Determine the current processing state for a topic subscription"""
        subscription_ready = topic.subscription_ready.done()
        leave_requested = topic.leave_requested.is_set()
        
        if not subscription_ready:
            return TopicProcessingState.WAITING_FOR_JOIN
        elif leave_requested:
            return TopicProcessingState.PROCESSING_LEAVE
        else:
            return TopicProcessingState.NORMAL_PROCESSING

    async def _process_topic_messages(self, topic_name: str) -> None:
        """Process messages for a specific topic subscription with three distinct processing states"""
        topic = self._topic_subscriptions[topic_name]
        self.logger.debug(f'Starting topic message processor for {topic.name}')
        
        try:
            while True:
                message = await topic.queue.get()
                
                current_state = self._determine_processing_state(topic)
                self.logger.debug(f'Processing message for topic {topic.name} in state {current_state.value}: {message}')
                
                if current_state == TopicProcessingState.WAITING_FOR_JOIN:
                    join_success = await self._handle_join_response_mode(topic, message)
                    if not join_success:
                        self.logger.debug(f'Exiting topic processor for {topic.name} due to join failure')
                        break
                        
                elif current_state == TopicProcessingState.PROCESSING_LEAVE:
                    leave_completed = await self._handle_leave_mode(topic, message)
                    if leave_completed:
                        self.logger.debug(f'Exiting topic processor for {topic.name} - leave completed')
                        break
                        
                elif current_state == TopicProcessingState.NORMAL_PROCESSING:
                    await self._handle_normal_message_mode(topic, message)
                
        except Exception as e:
            self.logger.exception(f'Error in topic message processor for {topic.name}: {e}')
            self._set_subscription_error(topic, e)
            self._unregister_topic(topic.name)

    async def _handle_join_response_mode(self, topic: TopicSubscription, message: ChannelMessage) -> bool:
        """Handle the initial join response message. Returns True if join succeeded, False if failed."""
        self.logger.debug(f'Handling join response for topic {topic.name}: {message}')
        
        is_join_success = (
            hasattr(message, 'event') and 
            message.event == PHXEvent.reply and
            message.payload.get('status') == 'ok'
        )
        
        if is_join_success:
            self._set_subscription_ready(topic)
            self.logger.info(f'Successfully subscribed to topic {topic.name}')
            return True
        else:
            error_message = "invalid topic"
            if hasattr(message, 'payload') and isinstance(message.payload, dict):
                response = message.payload.get('response', {})
                if isinstance(response, dict) and 'reason' in response:
                    error_message = response['reason']
            
            error = PHXTopicError(error_message)
            self._set_subscription_error(topic, error)
            self.logger.error(f'Failed to subscribe to topic {topic.name}: {error_message}')
            self._unregister_topic(topic.name)
            return False

    async def _handle_normal_message_mode(self, topic: TopicSubscription, message: ChannelMessage) -> None:
        """Handle normal message processing mode"""
        self.logger.debug(f'Processing normal message for topic {topic.name}: {message}')
        
        try:
            topic.current_callback_task = asyncio.create_task(topic.async_callback(message))
            await topic.current_callback_task
        except Exception as e:
            self.logger.exception(f'Error in topic callback for {topic.name}: {e}')
        finally:
            topic.current_callback_task = None

    async def _handle_leave_mode(self, topic: TopicSubscription, message: ChannelMessage) -> bool:
        """Handle leave processing mode. Returns True if leave is completed, False to continue draining."""
        self.logger.debug(f'Processing message during leave for topic {topic.name}: {message}')
        
        is_leave_response = (
            hasattr(message, 'event') and 
            message.event == PHXEvent.reply and
            hasattr(message, 'payload')
        )
        
        if is_leave_response:
            is_leave_success = message.payload.get('status') == 'ok'
            
            if is_leave_success:
                self.logger.info(f'Successfully unsubscribed from topic {topic.name}')
            else:
                self.logger.error(f'Failed to unsubscribe from topic {topic.name}: {message.payload}')
            
            if topic.current_callback_task and not topic.current_callback_task.done():
                self.logger.debug(f'Waiting for current callback to finish for topic {topic.name}')
                try:
                    await topic.current_callback_task
                except Exception as e:
                    self.logger.exception(f'Error waiting for callback to finish for {topic.name}: {e}')
            
            if topic.unsubscribe_completed and not topic.unsubscribe_completed.done():
                if is_leave_success:
                    topic.unsubscribe_completed.set_result(None)
                else:
                    topic.unsubscribe_completed.set_exception(PHXTopicError(f'Failed to unsubscribe: {message.payload}'))
            
            return True  
        else:
            self.logger.debug(f'Ignoring queued message for leaving topic {topic.name}: {message}')
            return False  

    def _unregister_topic(self, topic_name: str) -> None:
        """Unregister a topic subscription"""
        if topic_name in self._topic_subscriptions:
            topic_subscription = self._topic_subscriptions[topic_name]
            if topic_subscription.process_topic_messages_task:
                topic_subscription.process_topic_messages_task.cancel()
            del self._topic_subscriptions[topic_name]
            self.logger.info(f'Unregistered topic {topic_name}')
        else:
            self.logger.warning(f'Topic {topic_name} not found in _topic_subscriptions')

    async def process_websocket_messages(self) -> None:
        self.logger.debug('Starting websocket message loop')
        async for socket_message in self.connection:
            phx_message = self._parse_message(socket_message)
            self.logger.debug(f'Processing message - {phx_message=}')
            topic = phx_message.topic
            
            if topic in self._topic_subscriptions:
                topic_subscription = self._topic_subscriptions[topic]
                await topic_subscription.queue.put(phx_message)

    def get_current_subscriptions(self) -> dict[str, TopicSubscription]:
        """Return a copy of the current topic subscriptions"""
        return self._topic_subscriptions.copy()

    async def subscribe_to_topic(self, topic: str, async_callback: Callable[[ChannelMessage], Awaitable[None]]) -> None:
        """Subscribe to a topic with the given async callback"""
        
        if topic in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} already subscribed')
        
        topic_queue = Queue()
        subscription_ready_future = self._loop.create_future()
        
        topic_subscription = TopicSubscription(
            name=topic,
            async_callback=async_callback,
            queue=topic_queue,
            subscription_ready=subscription_ready_future,
            process_topic_messages_task=self._loop.create_task(
                self._process_topic_messages(topic)
            )
        )
        
        self._topic_subscriptions[topic] = topic_subscription
        topic_join_message = make_message(event=PHXEvent.join, topic=topic)
        await self._send_message(self.connection, topic_join_message)
        
        try:
            await subscription_ready_future  # Just wait for readiness, no return value needed
        except Exception as e:
            self.logger.error(f'Error during subscribe to {topic}: {e}')
            self._unregister_topic(topic)
            raise

    async def unsubscribe_from_topic(self, topic: str) -> None:
        """Unsubscribe from a topic"""
        
        if topic not in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} not subscribed')
        
        topic_subscription = self._topic_subscriptions[topic]
        
        # Create a future to wait for the leave response
        unsubscribe_completed_future = self._loop.create_future()
        topic_subscription.unsubscribe_completed = unsubscribe_completed_future
        
        # Send leave message first
        topic_leave_message = make_message(event=PHXEvent.leave, topic=topic)
        await self._send_message(self.connection, topic_leave_message)
        
        # Signal the existing task to start leave process
        topic_subscription.leave_requested.set()
        
        try:
            await unsubscribe_completed_future  # Just wait for completion, no return value needed
        except Exception as e:
            self.logger.error(f'Error during unsubscribe from {topic}: {e}')
            raise
        finally:
            self._unregister_topic(topic)


    async def _start_processing(self) -> None:

        await self.process_websocket_messages()
