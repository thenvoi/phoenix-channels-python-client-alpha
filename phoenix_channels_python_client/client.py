import asyncio
import logging
from asyncio import AbstractEventLoop, Queue
from concurrent.futures import Executor
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
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus, TopicSubscribeResult, TopicSubscription
from phoenix_channels_python_client.utils import make_message


class PHXChannelsClient:
    channel_socket_url: str
    logger: Logger

    _topic_subscriptions: dict[str, TopicSubscription]
    _loop: AbstractEventLoop
    _executor_pool: Optional[Executor]

    def __init__(
        self,
        channel_socket_url: str,
        event_loop: Optional[AbstractEventLoop] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.channel_socket_url = channel_socket_url
        self.connection = None
        self._topic_subscriptions = {}
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

        # Cancel all topic subscription tasks
        for topic, subscription in self._topic_subscriptions.items():
            if subscription.process_topic_messages_task:
                subscription.process_topic_messages_task.cancel()
                self.logger.debug(f'Cancelled topic subscription task for {topic}')
        
        self._topic_subscriptions.clear()


    def _set_subscription_result(self, topic_subscription: TopicSubscription, result: TopicSubscribeResult) -> None:
        """Safely set subscription result only if not already done"""
        if not topic_subscription.subscription_result.done():
            topic_subscription.subscription_result.set_result(result)

    def _set_subscription_error(self, topic_subscription: TopicSubscription, error: Exception) -> None:
        """Safely set subscription error only if not already done"""
        if not topic_subscription.subscription_result.done():
            topic_subscription.subscription_result.set_exception(error)

    async def _process_topic_messages(self, topic_name: str) -> None:
        """Process messages for a specific topic subscription"""
        topic=self._topic_subscriptions[topic_name]
        self.logger.debug(f'Starting topic message processor for {topic.name}')
        
        try:
            # Wait for the first message which should be the join reply
            first_message = await topic.queue.get()
            topic.queue.task_done()
            self.logger.debug(f'Got first message for topic {topic.name}: {first_message}')
            
            # Check if it's a successful join reply
            is_join_success = (
                hasattr(first_message, 'event') and 
                first_message.event == PHXEvent.reply and
                first_message.payload.get('status') == 'ok'
            )
            
            if is_join_success:
                # Successfully joined topic
                result = TopicSubscribeResult(SubscriptionStatus.SUCCESS, first_message)
                self._set_subscription_result(topic, result)
                self.logger.info(f'Successfully subscribed to topic {topic.name}')
                
                # Continue processing messages for this topic
                await self._process_ongoing_messages(topic)
            else:
                # Failed to join topic or unexpected message
                result = TopicSubscribeResult(SubscriptionStatus.FAILED, first_message)
                self._set_subscription_result(topic, result)
                self.logger.error(f'Failed to subscribe to topic {topic.name}: {first_message.payload if hasattr(first_message, "payload") else first_message}')
                self._unregister_topic(topic.name)
                
        except Exception as e:
            self.logger.exception(f'Error in topic message processor for {topic.name}: {e}')
            self._set_subscription_error(topic, e)
            self._unregister_topic(topic.name)

    async def _process_ongoing_messages(self, topic: TopicSubscription) -> None:
        """Process ongoing messages for a successfully subscribed topic"""
        
        try:
            while True:
                message = await topic.queue.get()
                self.logger.debug(f'Processing message for topic {topic.name}: {message}')
                
                try:
                    await topic.async_callback(message)
                except Exception as e:
                    self.logger.exception(f'Error in topic callback for {topic.name}: {e}')
                
                topic.queue.task_done()
        except asyncio.CancelledError:
            self.logger.debug(f'Topic message processor for {topic.name} cancelled')
        except Exception as e:
            self.logger.exception(f'Error processing ongoing messages for {topic.name}: {e}')

    async def _process_leave_response(self, topic_name: str, leave_result_future) -> None:
        """Process the server response to a leave message"""
        topic = self._topic_subscriptions.get(topic_name)
        if not topic:
            self.logger.warning(f'Topic {topic_name} not found during leave processing')
            return
        
        self.logger.debug(f'Waiting for leave response for topic {topic.name}')
        
        try:
            # Wait for the leave response message
            response_message = await topic.queue.get()
            topic.queue.task_done()
            self.logger.debug(f'Got leave response for topic {topic.name}: {response_message}')
            
            # Check if it's a successful leave reply
            is_leave_success = (
                hasattr(response_message, 'event') and 
                response_message.event == PHXEvent.reply and
                response_message.payload.get('status') == 'ok'
            )
            
            if is_leave_success:
                # Successfully left topic
                result = TopicSubscribeResult(SubscriptionStatus.SUCCESS, response_message)
                if not leave_result_future.done():
                    leave_result_future.set_result(result)
                self.logger.info(f'Successfully unsubscribed from topic {topic.name}')
            else:
                # Failed to leave topic or unexpected message
                result = TopicSubscribeResult(SubscriptionStatus.FAILED, response_message)
                if not leave_result_future.done():
                    leave_result_future.set_result(result)
                self.logger.error(f'Failed to unsubscribe from topic {topic.name}: {response_message.payload if hasattr(response_message, "payload") else response_message}')
                
        except Exception as e:
            self.logger.exception(f'Error processing leave response for {topic.name}: {e}')
            if not leave_result_future.done():
                leave_result_future.set_exception(e)

    def _unregister_topic(self, topic_name: str) -> None:
        """Unregister a topic subscription"""
        if topic_name in self._topic_subscriptions:
            topic_subscription = self._topic_subscriptions[topic_name]
            if topic_subscription.process_topic_messages_task:
                topic_subscription.process_topic_messages_task.cancel()
            del self._topic_subscriptions[topic_name]
            self.logger.info(f'Unregistered topic {topic_name}')

    async def process_websocket_messages(self) -> None:
        self.logger.debug('Starting websocket message loop')
        async for socket_message in self.connection:
            print(socket_message)
            phx_message = self._parse_message(socket_message)
            self.logger.debug(f'Processing message - {phx_message=}')
            topic = phx_message.topic
            
            # Route message to topic subscription if it exists
            if topic in self._topic_subscriptions:
                topic_subscription = self._topic_subscriptions[topic]
                await topic_subscription.queue.put(phx_message)

    def get_current_subscriptions(self) -> dict[str, TopicSubscription]:
        """Return a copy of the current topic subscriptions"""
        return self._topic_subscriptions.copy()

    async def subscribe_to_topic(self, topic: str, async_callback: Callable[[ChannelMessage], Awaitable[None]]) -> TopicSubscribeResult:
        """Subscribe to a topic with the given async callback"""
        
        if topic in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} already subscribed')
        
        topic_queue = Queue()
        subscription_result_future = self._loop.create_future()
        
        topic_subscription = TopicSubscription(
            name=topic,
            async_callback=async_callback,
            queue=topic_queue,
            subscription_result=subscription_result_future,
            process_topic_messages_task=self._loop.create_task(
                self._process_topic_messages(topic)
            )
        )
        
        self._topic_subscriptions[topic] = topic_subscription
        topic_join_message = make_message(event=PHXEvent.join, topic=topic)
        await self._send_message(self.connection, topic_join_message)
        
        try:
            result = await subscription_result_future
            return result
        except Exception as e:
            self._unregister_topic(topic)
            raise

    async def unsubscribe_from_topic(self, topic: str) -> TopicSubscribeResult:
        """Unsubscribe from a topic"""
        
        if topic not in self._topic_subscriptions:
            raise PHXTopicError(f'Topic {topic} not subscribed')
        
        topic_subscription = self._topic_subscriptions[topic]
        
        # Create a future to wait for the leave response
        leave_result_future = self._loop.create_future()
        
        # Cancel the existing message processing task
        if topic_subscription.process_topic_messages_task:
            topic_subscription.process_topic_messages_task.cancel()
        
        # Create a new task to handle the leave response
        leave_response_task = self._loop.create_task(
            self._process_leave_response(topic, leave_result_future)
        )
        
        # Update the subscription with the new task
        topic_subscription.process_topic_messages_task = leave_response_task
        
        # Send leave message
        topic_leave_message = make_message(event=PHXEvent.leave, topic=topic)
        await self._send_message(self.connection, topic_leave_message)
        
        try:
            result = await leave_result_future
            return result
        except Exception as e:
            self.logger.error(f'Error during unsubscribe from {topic}: {e}')
            raise
        finally:
            # Always clean up the subscription
            self._unregister_topic(topic)


    async def _start_processing(self) -> None:
        # self._loop.add_signal_handler(signal.SIGTERM, partial(self.shutdown, reason='SIGTERM'))
        # self._loop.add_signal_handler(signal.SIGINT, partial(self.shutdown, reason='Keyboard Interrupt'))


        await self.process_websocket_messages()
