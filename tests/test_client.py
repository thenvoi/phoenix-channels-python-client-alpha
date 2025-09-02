import pytest
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.topic_subscription import SubscriptionStatus
from phoenix_channels_python_client.exceptions import PHXTopicError


@pytest.mark.asyncio
async def test_subscribe_to_existing_topic(phoenix_server):
    """Test subscribing to a topic that exists on the server"""
    # 1. WebSocket server is already running via fixture
    
    # 2. Connect to it from the client
    async with PHXChannelsClient(phoenix_server.url) as client:
        # Mock callback for received messages
        async def test_callback(message):
            print(f"Received message: {message}")
        
        # 3. Subscribe to a test topic and verify response
        result = await client.subscribe_to_topic("test-topic", test_callback)
        
        # Assert the reply is the expected reply (successful)
        assert result.status == SubscriptionStatus.SUCCESS
        assert result.result_message.event.value == "phx_reply"
        assert result.result_message.payload["status"] == "ok"
        
        # Assert the topic was added to subscriptions with success status
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
async def test_subscribe_to_invalid_topic(phoenix_server):
    """Test subscribing to an invalid topic should raise PHXTopicError"""
    # Connect to the server
    async with PHXChannelsClient(phoenix_server.url) as client:
        # Mock callback for received messages
        async def test_callback(message):
            print(f"Received message: {message}")
        
        # Try to subscribe to an invalid topic - should raise PHXTopicError
        with pytest.raises(PHXTopicError) as exc_info:
            await client.subscribe_to_topic("invalid-topic", test_callback)
        
        # Verify the exception message contains "invalid topic"
        assert "invalid topic" in str(exc_info.value).lower()
