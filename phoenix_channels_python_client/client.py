import asyncio
import logging
from asyncio import AbstractEventLoop, Queue
from enum import Enum
from types import TracebackType
from typing import  Callable, Optional, Type, Union, Awaitable
from websockets import ClientConnection

from websockets import connect

from phoenix_channels_python_client.exceptions import PHXConnectionError, PHXTopicError
from phoenix_channels_python_client.phx_messages import (
    ChannelMessage,
    PHXEvent,
    PHXEventMessage,
)
from phoenix_channels_python_client.protocol_handler import PHXProtocolHandler
from phoenix_channels_python_client.topic_subscription import TopicSubscription
from phoenix_channels_python_client.utils import make_message


class TopicProcessingState(Enum):
    WAITING_FOR_JOIN = "waiting_for_join"
    PROCESSING_LEAVE = "processing_leave"
    NORMAL_PROCESSING = "normal_processing"


class PHXChannelsClient:
    def __init__(
        self,
        channel_socket_url: str,
        event_loop: Optional[AbstractEventLoop] = None,
        protocol_version: str = "1.0",
    ):
        self.logger = logging.getLogger(__name__)
        self.channel_socket_url = channel_socket_url
        self.connection = None
        self._topic_subscriptions: dict[str, TopicSubscription] = {}
        self._loop = event_loop or asyncio.get_event_loop()
        self._message_routing_task=None
        self._protocol_handler = PHXProtocolHandler(protocol_version)
        self._ref_counter = 0

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
        self.logger.debug(f'Serialising {message=} to Phoenix Channels v{self._protocol_handler.get_protocol_version()} format')
        text_message = self._protocol_handler.serialize_message(message)

        self.logger.debug(f'Sending as TEXT frame: {text_message}')
        await websocket.send(text_message)

    def _parse_message(self, socket_message: Union[str, bytes]) -> ChannelMessage:
        self.logger.debug(f'Got message - {socket_message=}')
        message = self._protocol_handler.parse_message(socket_message)
        self.logger.debug(f'Decoded message - {message=}')
        return message

    async def shutdown(
        self,
        reason: str,
    ) -> None:
        self.logger.info(f'Event loop shutting down! {reason=}')
        
        topics_to_unsubscribe = list(self._topic_subscriptions.keys())
        if topics_to_unsubscribe:
            unsubscribe_tasks = [
                self.unsubscribe_from_topic(topic) 
                for topic in topics_to_unsubscribe
            ]
            
            results = await asyncio.gather(*unsubscribe_tasks, return_exceptions=True)
            
            for topic, result in zip(topics_to_unsubscribe, results):
                if isinstance(result, Exception):
                    self.logger.warning(f'Failed to unsubscribe from topic {topic} during shutdown: {result}')
                    self._unregister_topic(topic)
                else:
                    self.logger.debug(f'Successfully unsubscribed from topic {topic} during shutdown')
        
        if self._message_routing_task and not self._message_routing_task.done():
            self._message_routing_task.cancel()
            try:
                await self._message_routing_task
            except asyncio.CancelledError:
                self.logger.debug('Message routing task cancelled during shutdown')
        
        await self.connection.close()
        self.connection = None


    def _set_subscription_ready(self, topic_subscription: TopicSubscription) -> None:
        if not topic_subscription.subscription_ready.done():
            topic_subscription.subscription_ready.set_result(None)

    def _set_subscription_error(self, topic_subscription: TopicSubscription, error: Exception) -> None:
        if not topic_subscription.subscription_ready.done():
            topic_subscription.subscription_ready.set_exception(error)

    def _determine_processing_state(self, topic: TopicSubscription) -> TopicProcessingState:
        subscription_ready = topic.subscription_ready.done()
        leave_requested = topic.leave_requested.is_set()
        
        if not subscription_ready:
            return TopicProcessingState.WAITING_FOR_JOIN
        elif leave_requested:
            return TopicProcessingState.PROCESSING_LEAVE
        else:
            return TopicProcessingState.NORMAL_PROCESSING

    async def _process_topic_messages(self, topic_name: str) -> None:
        topic = self._topic_subscriptions[topic_name]
        self.logger.debug(f'Starting topic message processor for {topic.name}')
        
        try:
            while True:
                message = await topic.queue.get()
                
                current_state = self._determine_processing_state(topic)
                self.logger.debug(f'Processing message for topic {topic.name} in state {current_state.value}: {message}')
                
                if current_state == TopicProcessingState.WAITING_FOR_JOIN:
                    await self._handle_join_response_mode(topic, message)

                elif current_state == TopicProcessingState.PROCESSING_LEAVE:
                    try:
                        await self._handle_leave_mode(topic, message)
                    except PHXTopicError:
                        self.logger.debug(f'Exiting topic processor for {topic.name} - leave completed')
                        break
                        
                elif current_state == TopicProcessingState.NORMAL_PROCESSING:
                    await self._handle_normal_message_mode(topic, message)
                
        except Exception as e:
            self.logger.exception(f'Error in topic message processor for {topic.name}: {e}')
            self._unregister_topic(topic.name)

    async def _handle_join_response_mode(self, topic: TopicSubscription, message: ChannelMessage) -> None:
        self.logger.debug(f'Handling join response for topic {topic.name}: {message}')
        
        if not isinstance(message, PHXEventMessage) or message.event != PHXEvent.reply:
            raise PHXTopicError(f'Unexpected message type in join response mode: {message}')
        
        if message.payload.get('status') == 'ok':
            self._set_subscription_ready(topic)
            self.logger.info(f'Successfully subscribed to topic {topic.name}')
        else:
            response = message.payload.get('response', {})
            error_message = response.get('reason', 'invalid topic') if isinstance(response, dict) else 'invalid topic'
            
            error = PHXTopicError(error_message)
            self._set_subscription_error(topic, error)
            self.logger.error(f'Failed to subscribe to topic {topic.name}: {error_message}')
            raise error

    async def _handle_normal_message_mode(self, topic: TopicSubscription, message: ChannelMessage) -> None:
        self.logger.debug(f'Processing normal message for topic {topic.name}: {message}')
        
        try:
            topic.current_callback_task = asyncio.create_task(topic.async_callback(message))
            await topic.current_callback_task
        except Exception as e:
            self.logger.exception(f'Error in topic callback for {topic.name}: {e}')
        finally:
            topic.current_callback_task = None

    async def _handle_leave_mode(self, topic: TopicSubscription, message: ChannelMessage) -> None:
        self.logger.debug(f'Processing message during leave for topic {topic.name}: {message}')
        
        if not isinstance(message, PHXEventMessage) or message.event != PHXEvent.reply:
            self.logger.debug(f'Ignoring queued message for leaving topic {topic.name}: {message}')
            return
        
        is_leave_success = message.payload.get('status') == 'ok'
        
        if is_leave_success:
            self.logger.info(f'Successfully unsubscribed from topic {topic.name}')
            
            if topic.current_callback_task and not topic.current_callback_task.done():
                self.logger.debug(f'Waiting for current callback to finish for topic {topic.name}')
                try:
                    await topic.current_callback_task
                except Exception as e:
                    self.logger.exception(f'Error waiting for callback to finish for {topic.name}: {e}')
            
            if topic.unsubscribe_completed and not topic.unsubscribe_completed.done():
                topic.unsubscribe_completed.set_result(None)
                
        else:
            self.logger.error(f'Failed to unsubscribe from topic {topic.name}: {message.payload}')
            if topic.unsubscribe_completed and not topic.unsubscribe_completed.done():
                topic.unsubscribe_completed.set_exception(PHXTopicError(f'Failed to unsubscribe: {message.payload}'))
            raise PHXTopicError(f'Failed to unsubscribe: {message.payload}')

    def _unregister_topic(self, topic_name: str) -> None:
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
        return self._topic_subscriptions.copy()
    
    def get_protocol_handler(self) -> PHXProtocolHandler:
        return self._protocol_handler
    
    def _generate_ref(self) -> str:
        """Generate unique reference for Phoenix Channels messages."""
        self._ref_counter += 1
        return str(self._ref_counter)

    async def subscribe_to_topic(self, topic: str, async_callback: Callable[[ChannelMessage], Awaitable[None]]) -> None:
        if topic in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} already subscribed')
        
        topic_queue = Queue()
        subscription_ready_future = self._loop.create_future()
        join_ref = self._generate_ref()
        
        topic_subscription = TopicSubscription(
            name=topic,
            async_callback=async_callback,
            queue=topic_queue,
            subscription_ready=subscription_ready_future,
            join_ref=join_ref,
            process_topic_messages_task=self._loop.create_task(
                self._process_topic_messages(topic)
            )
        )
        
        self._topic_subscriptions[topic] = topic_subscription
        topic_join_message = make_message(event=PHXEvent.join, topic=topic, ref=join_ref, join_ref=join_ref)
        await self._send_message(self.connection, topic_join_message)
        
        try:
            await subscription_ready_future
        except Exception as e:
            self.logger.error(f'Error during subscribe to {topic}: {e}')
            self._unregister_topic(topic)
            raise

    async def unsubscribe_from_topic(self, topic: str) -> None:
        if topic not in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} not subscribed')
        
        topic_subscription = self._topic_subscriptions[topic]
        
        unsubscribe_completed_future = self._loop.create_future()
        topic_subscription.unsubscribe_completed = unsubscribe_completed_future
        
        leave_ref = self._generate_ref()
        topic_leave_message = make_message(event=PHXEvent.leave, topic=topic, ref=leave_ref)
        await self._send_message(self.connection, topic_leave_message)
        
        topic_subscription.leave_requested.set()
        
        try:
            await unsubscribe_completed_future
        except Exception as e:
            self.logger.error(f'Error during unsubscribe from {topic}: {e}')
            raise
        finally:
            self._unregister_topic(topic)


    async def _start_processing(self) -> None:
        await self.process_websocket_messages()
