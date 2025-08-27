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
from phoenix_channels_python_client.api import nothing
from phoenix_channels_python_client.client import PHXChannelsClient

import asyncio
import os
import logging
# from phx_events.client import PHXChannelsClient
# from phx_events.phx_messages import ChannelMessage, Event, Topic
from dotenv import load_dotenv
nothing()
# # Configure logging to see phx_events internal operations
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

# async def message_handler(message: ChannelMessage) -> None:
#     """Handle incoming messages from the WebSocket."""
#     logger.debug(f"Processing message: {message.event} on {message.topic}")




# async def participant_added_handler(message: ChannelMessage) -> None:
#     """Handle participant added events (when someone joins a room)."""
#     logger.debug(f"Participant added to room: {message.topic}")


# async def participant_removed_handler(message: ChannelMessage) -> None:
#     """Handle participant removed events (when someone leaves a room)."""
#     logger.debug(f"Participant removed from room: {message.topic}")


# async def room_added_handler(message: ChannelMessage) -> None:
#     """Handle room added events (when user is added to a new room)."""
#     logger.debug(f"User added to room: {message.topic}")


# async def room_removed_handler(message: ChannelMessage) -> None:
#     """Handle room removed events (when user is removed from a room)."""
#     logger.debug(f"User removed from room: {message.topic}")


async def main():
    """Main client function."""
    load_dotenv()
    
    api_key = os.getenv("THENVOI_API_KEY", "1234")  # Default for demo
    # user_id = os.getenv("THENVOI_USER_ID", "your-room-id")
    
    ws_base_url = os.getenv("THENVOI_WS_URL", "ws://localhost:4000")
    
    # room_id = os.getenv("THENVOI_ROOM_ID", "ddaf887b-2f86-4df1-a27d-4f8f0a360869")
    
    # logger.info(f"🔍 Debug - Environment values:")
    # logger.info(f"   API Key: {api_key[:10] + '...' if api_key and len(api_key) > 10 else api_key}")
    # logger.info(f"   WS Base URL: {ws_base_url}")
    # logger.info(f"   User ID: {user_id}")
    
    if not api_key:
        logger.error("❌ Error: THENVOI_API_KEY environment variable is required")
        logger.error("Please set it in your .env file or environment")
        return
    
    ws_url_with_auth = f"{ws_base_url}?api_key={api_key}&vsn=1.0.0"
    
    # logger.info(f"🚀 Starting minimal phx-events client...")
    # logger.info(f"   WebSocket URL: {ws_url_with_auth}")
    # logger.info(f"   User ID: {user_id}")
    # logger.info(f"   Room ID: {room_id}")
    # logger.info(f"   Topics: tasks:{user_id}, room_participants:{room_id}, user_rooms:{user_id}")
    # logger.info("-" * 50)
    
    try:
        logger.debug("Attempting to connect to Phoenix WebSocket...")
        async with PHXChannelsClient(ws_url_with_auth) as client:
            logger.info("🔗 Connected to Thenvoi platform!")
            
            
    except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        logger.exception("Full exception details:")
        raise
    except KeyboardInterrupt:
        logger.info("\n👋 Client stopped by user")



if __name__ == "__main__":
    """Run the minimal client."""
    asyncio.run(main())
