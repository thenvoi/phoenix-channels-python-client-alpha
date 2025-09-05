#!/usr/bin/env python3
"""
Minimal Phoenix Events Client for Thenvoi Platform

This example demonstrates how to use the phx-events library to connect
to the Thenvoi platform's WebSocket API and listen to real-time events.

Usage:
    python sdk/examples/minimal_phx_events_client.py

Environment Variables Required:
    THENVOI_API_KEY - Your API key for authentication
    THENVOI_USER_ID - User ID to listen for task updates (optional)
    LOG_LEVEL - Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) - defaults to INFO

Logging:
    - Console output shows all logs
    - File output saved to 'phx_events_debug.log'
    - Set LOG_LEVEL=DEBUG to see all phx_events internal operations
    - Set LOG_LEVEL=INFO for normal operation with some debug info
"""
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.protocol_handler import PhoenixChannelsProtocolVersion

import asyncio
import os
import logging
from dotenv import load_dotenv
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
log_level_map = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

root_logger = logging.getLogger()
root_logger.setLevel(log_level_map.get(log_level, logging.DEBUG))

for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

console_handler = logging.StreamHandler()
console_handler.setLevel(log_level_map.get(log_level, logging.DEBUG))

file_handler = logging.FileHandler('phx_events_debug.log')
file_handler.setLevel(log_level_map.get(log_level, logging.DEBUG))

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers to root logger
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

# phx_client_logger = logging.getLogger('phx_events.client')
# phx_client_logger.setLevel(log_level_map.get(log_level, logging.DEBUG))
# phx_client_logger.propagate = True

logger = logging.getLogger(__name__)
logger.setLevel(log_level_map.get(log_level, logging.DEBUG))


async def main():
    """Main client function."""
    load_dotenv()
    
    api_key = os.getenv("THENVOI_API_KEY", "1234")  # Default for demo
    # user_id = os.getenv("THENVOI_USER_ID", "your-room-id")
    
    ws_base_url = os.getenv("THENVOI_WS_URL", "ws://localhost:4000")
    
    
    
    if not api_key:
        logger.error("❌ Error: THENVOI_API_KEY environment variable is required")
        logger.error("Please set it in your .env file or environment")
        return
    
    try:
        logger.debug("Attempting to connect to Phoenix WebSocket...")
        async with PHXChannelsClient(ws_base_url, api_key=api_key, protocol_version=PhoenixChannelsProtocolVersion.V2) as client:
            logger.info("🔗 Connected to Thenvoi platform!")
            await client.subscribe_to_topic("room_participants:your-room-id", lambda message: logger.info(f"📨 Received: {message}"))
            logger.info("✅ Successfully subscribed! Waiting for messages...")
            await asyncio.sleep(1)
            # await asyncio.sleep(1000)
            # await client.unsubscribe_from_topic("room_participants:your-room-id")
            # print(client.get_current_subscriptions())
            # await asyncio.sleep(10)
            # await client.subscribe_to_topic("room_participants:ea906102-8cd3-47e5-a051-49e5a7d6627d", lambda message: print(message))
            # print(client.get_current_subscriptions())
            # await asyncio.sleep(100)
            
            
    except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        logger.exception("Full exception details:")
        raise
    except KeyboardInterrupt:
        logger.info("\n👋 Client stopped by user")



if __name__ == "__main__":
    """Run the minimal client."""
    asyncio.run(main())
