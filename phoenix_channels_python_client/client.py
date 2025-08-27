import asyncio
import logging
from asyncio import AbstractEventLoop, Event, Queue, Task, Future
from concurrent.futures import Executor, ThreadPoolExecutor
from functools import partial
import inspect
from logging import Logger
import signal
from types import TracebackType
from typing import Awaitable, Callable, cast, Optional, Type, Union
from websockets import ClientConnection

from websockets import connect

from phoenix_channels_python_client import json_handler
from phoenix_channels_python_client.exceptions import PHXConnectionError, PHXTopicTooManyRegistrationsError, TopicClosedError
from phoenix_channels_python_client.phx_messages import (
    ChannelEvent,
    ChannelHandlerFunction,
    ChannelMessage,
    CoroutineHandler,
    EventHandlerConfig,
    ExecutorHandler,
    PHXEvent,
    Topic,
)
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus, TopicSubscribeResult, TopicSubscription
from phoenix_channels_python_client.utils import make_message


class PHXChannelsClient:
    channel_socket_url: str
    logger: Logger

    _topic_subscriptions: dict[Topic, TopicSubscription]
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

    async def _event_processor(self, event: ChannelEvent) -> None:
        """Coroutine used to create tasks that process the given event

        Runs all the handlers in the thread_pool and logs any exceptions.
        """
        # Make all tasks wait until the _client_start_event is set
        # This prevents trying to do any processing until we want the "workers" to start
        self.logger.debug(f'{event} Worker - Waiting for client start!')
        await self._client_start_event.wait()

        self.logger.debug(f'{event} Worker - Started!')
        event_handler_config = self._event_handler_config[event]

        # Keep running until we ask all the tasks to stop
        while True:
            # wait until there's a message on the queue to process
            message = await event_handler_config.queue.get()
            self.logger.debug(f'{event} Worker - Got {message=}')

            # We run all the default handlers as well as the specific topic handlers
            event_handlers: list[ChannelHandlerFunction] = event_handler_config.default_handlers.copy()
            if topic_handlers := event_handler_config.topic_handlers.get(message.topic):
                event_handlers.extend(topic_handlers)

            event_tasks = []
            task: Union[Task[None], Awaitable[None]]
            # Run all the event handlers in self.thread_pool managed by AsyncIO or as tasks
            for event_handler in event_handlers:
                if inspect.iscoroutinefunction(event_handler):
                    event_handler = cast(CoroutineHandler, event_handler)
                    task = self._loop.create_task(event_handler(message, self))
                else:
                    event_handler = cast(ExecutorHandler, event_handler)
                    handler_task = partial(event_handler, message, self)
                    task = self._loop.run_in_executor(self._executor_pool, handler_task)

                event_tasks.append(task)

            # Wait until the handlers finish running & await the results to handle errors
            for handler_future in asyncio.as_completed(event_tasks):
                try:
                    await handler_future
                except Exception as exception:
                    self.logger.exception(f'Error executing handler - {exception=}')

            # Let the queue know the task is done being processed
            event_handler_config.queue.task_done()

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

    def register_event_handler(
        self,
        event: ChannelEvent,
        handlers: list[ChannelHandlerFunction],
        topic: Optional[Topic] = None,
    ) -> None:
        if event not in self._event_handler_config:
            # Create the coroutine that will become a task
            event_coroutine = self._event_processor(event)

            # Create the default EventHandlerConfig
            self._event_handler_config[event] = EventHandlerConfig(
                queue=Queue(),
                default_handlers=[],
                topic_handlers={},
                task=self._loop.create_task(event_coroutine),
            )

        handler_config = self._event_handler_config[event]
        # If there is a topic to be registered for - add the handlers to the topic handler
        if topic is not None:
            handler_config.topic_handlers.setdefault(topic, []).extend(handlers)
        else:
            # otherwise, add them to the default handlers
            handler_config.default_handlers.extend(handlers)





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
                    topic.callback(message)
                except Exception as e:
                    self.logger.exception(f'Error in topic callback for {topic.name}: {e}')
                
                topic.queue.task_done()
        except asyncio.CancelledError:
            self.logger.debug(f'Topic message processor for {topic.name} cancelled')
        except Exception as e:
            self.logger.exception(f'Error processing ongoing messages for {topic.name}: {e}')

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
        print("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

        async for socket_message in self.connection:
            print(socket_message)
            phx_message = self._parse_message(socket_message)
            self.logger.debug(f'Processing message - {phx_message=}')
            topic = phx_message.topic
            
            # Route message to topic subscription if it exists
            if topic in self._topic_subscriptions:
                topic_subscription = self._topic_subscriptions[topic]
                await topic_subscription.queue.put(phx_message)

    async def subscribe_to_topic(self, topic: str, callback: Callable[[ChannelMessage], None]) -> TopicSubscribeResult:
        """Subscribe to a topic with the given callback"""
        
        # Check if topic is already subscribed
        if topic in self._topic_subscriptions:
            raise PHXTopicTooManyRegistrationsError(f'Topic {topic} already subscribed')
        
        # Create the topic subscription
        topic_queue = Queue()
        subscription_result_future = self._loop.create_future()
        
        topic_subscription = TopicSubscription(
            name=topic,
            callback=callback,
            queue=topic_queue,
            subscription_result=subscription_result_future,
            process_topic_messages_task=self._loop.create_task(
                self._process_topic_messages(topic)
            )
        )
        
        
        # Add to subscriptions dictionary
        self._topic_subscriptions[topic] = topic_subscription
        
        # Send join message
        topic_join_message = make_message(event=PHXEvent.join, topic=topic)
        await self._send_message(self.connection, topic_join_message)
        
        # Wait for subscription result and return it
        try:
            result = await subscription_result_future
            return result
        except Exception as e:
            # Clean up on error
            self._unregister_topic(topic)
            raise


    async def _start_processing(self) -> None:
        # self._loop.add_signal_handler(signal.SIGTERM, partial(self.shutdown, reason='SIGTERM'))
        # self._loop.add_signal_handler(signal.SIGINT, partial(self.shutdown, reason='Keyboard Interrupt'))


        await self.process_websocket_messages()
