"""
Phoenix Channels Python Client

A Python client library for connecting to Phoenix Channels.
"""

from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.protocol_handler import PHXProtocolHandler

__version__ = "0.1.0"
__author__ = "Phoenix Channels Python Client"

__all__ = [
    "PHXChannelsClient", 
    "PHXProtocolHandler",
]
