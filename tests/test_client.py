import asyncio
import pytest
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus
from phoenix_channels_python_client.exceptions import PHXTopicError
from conftest import FakePhoenixServer


@pytest.mark.asyncio
async def test_subscribe_to_topic_succeeds_when_subscribing_to_valid_topic(phoenix_server: FakePhoenixServer):
    
    async with PHXChannelsClient(phoenix_server.url) as client:
        async def test_callback(message):
            print(f"Received message: {message}")
        
        result = await client.subscribe_to_topic("test-topic", test_callback)
        
        assert result.status == SubscriptionStatus.SUCCESS
        assert result.result_message.event.value == "phx_reply"
        assert result.result_message.payload["status"] == "ok"
        
        subscriptions = client.get_current_subscriptions()
        assert "test-topic" in subscriptions
        
        topic_subscription = subscriptions["test-topic"]
        assert topic_subscription.name == "test-topic"
        assert topic_subscription.async_callback == test_callback
        
        # Verify subscription result indicates success
        subscription_result = await topic_subscription.subscription_result
        assert subscription_result.status == SubscriptionStatus.SUCCESS
        
    # 4. Cleanup happens automatically via context manager


@pytest.mark.asyncio
async def test_subscribe_to_topic_raises_phxtopicerror_when_subscribing_to_unmatched_topic(phoenix_server: FakePhoenixServer):
    async with PHXChannelsClient(phoenix_server.url) as client:
        async def test_callback(message):
            print(f"Received message: {message}")
        
        with pytest.raises(PHXTopicError) as exc_info:
            await client.subscribe_to_topic("invalid-topic", test_callback)
        
        assert "unmatched topic" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_subscribe_to_topic_raises_phxtopicerror_when_subscribing_to_already_subscribed_topic(phoenix_server: FakePhoenixServer):
    async with PHXChannelsClient(phoenix_server.url) as client:
        async def test_callback(message):
            print(f"Received message: {message}")
        
        result = await client.subscribe_to_topic("test-topic", test_callback)
        assert result.status == SubscriptionStatus.SUCCESS
        
        with pytest.raises(PHXTopicError) as exc_info:
            await client.subscribe_to_topic("test-topic", test_callback)
        
        expected_message = "Topic test-topic already subscribed"
        assert str(exc_info.value) == expected_message


@pytest.mark.asyncio
async def test_unsubscribe_from_topic_succeeds_when_unsubscribing_from_subscribed_topic(phoenix_server: FakePhoenixServer):
    async with PHXChannelsClient(phoenix_server.url) as client:
        async def test_callback(message):
            print(f"Received message: {message}")
        
        subscribe_result = await client.subscribe_to_topic("test-topic", test_callback)
        assert subscribe_result.status == SubscriptionStatus.SUCCESS
        
        subscriptions = client.get_current_subscriptions()
        assert "test-topic" in subscriptions
        
        unsubscribe_result = await client.unsubscribe_from_topic("test-topic")
        
        assert unsubscribe_result.status == SubscriptionStatus.SUCCESS
        assert unsubscribe_result.result_message.event.value == "phx_reply"
        assert unsubscribe_result.result_message.payload["status"] == "ok"
        
        subscriptions = client.get_current_subscriptions()
        assert "test-topic" not in subscriptions


@pytest.mark.asyncio
async def test_callback_receives_message_when_server_sends_message_to_subscribed_topic(phoenix_server: FakePhoenixServer):

    received_messages = []
    callback_event = asyncio.Event()
    
    async def test_callback(message):
        received_messages.append(message)
        callback_event.set()
    
    async with PHXChannelsClient(phoenix_server.url) as client:
        # Subscribe to topic
        result = await client.subscribe_to_topic("test-topic", test_callback)
        assert result.status == SubscriptionStatus.SUCCESS
        
        test_payload = {"user_id": 123, "message": "Hello from server!"}
        await phoenix_server.simulate_server_event("test-topic", "new_message", test_payload)
        
        await callback_event.wait()
        
        # Verify callback was called with correct message
        assert len(received_messages) == 1
        message = received_messages[0]
        
        # Check message structure
        assert hasattr(message, 'topic')
        assert hasattr(message, 'event') 
        assert hasattr(message, 'payload')
        
        # Check message content
        assert message.topic == "test-topic"
        # Event could be either a PHXEvent enum or a string for custom events
        if hasattr(message.event, 'value'):
            assert message.event.value == "new_message"
        else:
            assert message.event == "new_message"
        assert message.payload == test_payload


@pytest.mark.asyncio
async def test_unsubscribe_from_topic_gracefully_allows_callback_to_finish_but_ignores_queued_events(phoenix_server: FakePhoenixServer):
    
    ARBITRARY_NUMBER_OF_EVENTS_THAT_WILL_SIMULATE_IGNORING_THEM_UNTIL_REACHING_THE_LEAVE_EVENT = 10
    
    received_messages = []
    callback_control_event = asyncio.Event()  # Controls when callback can complete
    first_callback_started = asyncio.Event()  # Signals when first callback has started
    
    async def test_callback(message):
        received_messages.append(message)
        if len(received_messages) == 1:  # Only set on first callback
            first_callback_started.set()
        # Wait for test to signal callback can complete
        await callback_control_event.wait()
    
    async with PHXChannelsClient(phoenix_server.url) as client:
        # 1. Subscribe to topic successfully
        result = await client.subscribe_to_topic("test-topic", test_callback)
        assert result.status == SubscriptionStatus.SUCCESS
        
        # 2. Send burst of events asynchronously using gather
        event_tasks = [
            phoenix_server.simulate_server_event("test-topic", "burst_event", {"event_id": i})
            for i in range(ARBITRARY_NUMBER_OF_EVENTS_THAT_WILL_SIMULATE_IGNORING_THEM_UNTIL_REACHING_THE_LEAVE_EVENT)
        ]
        await asyncio.gather(*event_tasks)
        
        # Wait for first callback to start (which blocks on callback_control_event)
        await first_callback_started.wait()
        
        # 3. Assert that queue size is correct (total events - 1 being processed)
        subscriptions = client.get_current_subscriptions()
        assert "test-topic" in subscriptions
        topic_subscription = subscriptions["test-topic"]
        assert topic_subscription.queue.qsize() == ARBITRARY_NUMBER_OF_EVENTS_THAT_WILL_SIMULATE_IGNORING_THEM_UNTIL_REACHING_THE_LEAVE_EVENT - 1
        
        # 4. Unsubscribe from topic
        unsubscribe_task = asyncio.create_task(client.unsubscribe_from_topic("test-topic"))
        
        # 5. Assert that callback is not done and topic is still in subscriptions
        assert topic_subscription.current_callback_task is not None
        assert not topic_subscription.current_callback_task.done()
        assert "test-topic" in client.get_current_subscriptions()
        
        # 6. Set the event to allow callback to complete
        callback_control_event.set()
        
        # 7. Wait for unsubscribe to complete and verify cleanup
        unsubscribe_result = await unsubscribe_task
        assert unsubscribe_result.status == SubscriptionStatus.SUCCESS
        assert "test-topic" not in client.get_current_subscriptions()
        
        # 8. Verify that only 1 message was processed since the callback was blocked
        assert len(received_messages) == 1
        assert received_messages[0].payload["event_id"] == 0
