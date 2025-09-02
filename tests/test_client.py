import pytest
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus
from phoenix_channels_python_client.exceptions import PHXTopicError


@pytest.mark.asyncio
async def test_subscribe_to_topic_succeeds_when_subscribing_to_valid_topic(phoenix_server):
    
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
async def test_subscribe_to_topic_raises_phxtopicerror_when_subscribing_to_unmatched_topic(phoenix_server):
    async with PHXChannelsClient(phoenix_server.url) as client:
        async def test_callback(message):
            print(f"Received message: {message}")
        
        with pytest.raises(PHXTopicError) as exc_info:
            await client.subscribe_to_topic("invalid-topic", test_callback)
        
        assert "unmatched topic" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_subscribe_to_topic_raises_phxtopicerror_when_subscribing_to_already_subscribed_topic(phoenix_server):
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
async def test_unsubscribe_from_topic_succeeds_when_unsubscribing_from_subscribed_topic(phoenix_server):
    """Test that unsubscribe_from_topic succeeds when unsubscribing from a subscribed topic"""
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
