#!/usr/bin/env python3
"""
Minimal Phoenix Events Client for Thenvoi Platform

This example demonstrates how to use the phx-events library to connect
to the Thenvoi platform's WebSocket API and listen to real-time events.

Usage:
    python examples/minimal_phx_events_client.py

This is a self-contained demo with hardcoded defaults for easy testing.
Modify the constants below to customize the connection settings.
"""
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.protocol_handler import PhoenixChannelsProtocolVersion

import asyncio
import logging

# Demo configuration - modify these values as needed
API_KEY = "1234"
WS_BASE_URL = "ws://localhost:4000/api/v1/socket/websocket"
LOG_LEVEL = logging.DEBUG

# Setup basic logging configuration
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('phx_events_debug.log')
    ]
)

logger = logging.getLogger(__name__)

async def message_handler(message):
    print(message)

async def main():
    """Main client function."""
    try:
        logger.debug("Attempting to connect to Phoenix WebSocket...")
        async with PHXChannelsClient(WS_BASE_URL, api_key=API_KEY, protocol_version=PhoenixChannelsProtocolVersion.V2) as client:
            logger.info("🔗 Connected to Thenvoi platform!")
            # await client.subscribe_to_topic("room_participants:9b51e799-0768-4bc3-881f-ccadbb1b4ea9", lambda message: logger.info(f"📨 Received: {message}"))
            await client.subscribe_to_topic("user_rooms:ca546c81-e2fc-41cb-802c-1260681c8e65", message_handler)
            logger.info("✅ Successfully subscribed! Press Ctrl+C to stop...")
            
            # Use the built-in convenience method instead of manual signal handling
            await client.run_forever()
            
    except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        logger.exception("Full exception details:")
        raise


if __name__ == "__main__":
    """Run the minimal client."""
    asyncio.run(main())
