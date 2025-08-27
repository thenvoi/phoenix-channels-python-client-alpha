import asyncio
import logging
from asyncio import AbstractEventLoop, Event, Queue, Task
from concurrent.futures import Executor, ThreadPoolExecutor
from functools import partial
import inspect
from logging import Logger
import signal
from types import TracebackType
from typing import Awaitable, Callable, cast, Optional, Type, Union
from urllib.parse import urlencode
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
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus, TopicRegistration, TopicSubscribeResult
from phoenix_channels_python_client.utils import make_message


class PHXChannelsClient:
    channel_socket_url: str
    logger: Logger

    _topic_registration_status: dict[Topic, TopicRegistration]
    _loop: AbstractEventLoop
    _executor_pool: Optional[Executor]
    _registration_queue: Queue

    def __init__(
        self,
        channel_socket_url: str,
        event_loop: Optional[AbstractEventLoop] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.channel_socket_url = channel_socket_url
        self.connection = None
        self._topic_registration_status = {}
        self._registration_queue = Queue()
        self._topic_registration_task = None
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
        self.shutdown('Leaving PHXChannelsClient context')

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

        if self.connection and not self.connection.closed:
            await self.connection.close()

        if self._topic_registration_task is not None:
            self._topic_registration_task.cancel()

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

    async def process_topic_registration_responses(self) -> None:
        while True:
            phx_message = await self._registration_queue.get()

            topic = phx_message.topic
            self.logger.info(f'Got topic {topic} join reply {phx_message=}')

            status = SubscriptionStatus.SUCCESS if phx_message.payload['status'] == 'ok' else SubscriptionStatus.FAILED
            status_message = 'SUCCEEDED' if status == SubscriptionStatus.SUCCESS else 'FAILED'
            self.logger.info(f'Topic registration {status_message} - {phx_message=}')

            # Set the topic status map
            topic_registration = self._topic_registration_status[topic]
            # Set topic status with the message
            topic_registration.result = TopicSubscribeResult(status, phx_message)
            # Notify any waiting tasks that the registration has been finalised and the status can be checked
            topic_registration.status_updated_event.set()
            # Tell the queue we've finished processing the current task
            self._registration_queue.task_done()

    def register_topic_subscription(self, topic: Topic) -> Event:
        if topic_status := self._topic_registration_status.get(topic):
            topic_ref = topic_status.connection_ref
            raise PHXTopicTooManyRegistrationsError(f'Topic {topic} already registered with {topic_ref=}')

        # Create an event to indicate when the reply has been processed
        status_updated_event = Event()

        self._topic_registration_status[topic] = TopicRegistration(status_updated_event=status_updated_event)

        return status_updated_event

    async def process_websocket_messages(self) -> None:
        self.logger.debug('Starting websocket message loop')
        print("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

        async for socket_message in self.connection:
            print(socket_message)
            phx_message = self._parse_message(socket_message)
            self.logger.debug(f'Processing message - {phx_message=}')
            topic = phx_message.topic

    async def subscribe_to_topic(self, topic: Topic,callback: Callable[[ChannelMessage], None]) -> None:
        status_updated_event = self.register_topic_subscription(topic)
        topic_join_message = make_message(event=PHXEvent.join, topic=topic)
        await self._send_message(self.connection, topic_join_message)
        await asyncio.sleep(10)


    async def _start_processing(self) -> None:
        # self._loop.add_signal_handler(signal.SIGTERM, partial(self.shutdown, reason='SIGTERM'))
        # self._loop.add_signal_handler(signal.SIGINT, partial(self.shutdown, reason='Keyboard Interrupt'))


        await self.process_websocket_messages()
