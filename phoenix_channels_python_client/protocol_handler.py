import logging
from typing import Union

from phoenix_channels_python_client import json_handler
from phoenix_channels_python_client.phx_messages import ChannelMessage
from phoenix_channels_python_client.utils import make_message


class PHXProtocolHandler:
    
    def __init__(self, protocol_version: str = "1.0"):
        self.protocol_version = protocol_version
        self.logger = logging.getLogger(f"{__name__}.ProtocolHandler")
        self.logger.debug(f"Initialized PHXProtocolHandler for protocol version {protocol_version}")
    
    def parse_message(self, raw_message: Union[str, bytes]) -> ChannelMessage:
        self.logger.debug(f'Parsing raw message: {raw_message}')
        
        try:
            parsed_data = json_handler.loads(raw_message)
            self.logger.debug(f'Decoded data: {parsed_data}')
            
            if self.protocol_version == "2.0":
                # v2.0 expects array format: [join_ref, msg_ref, topic, event, payload]
                if not isinstance(parsed_data, list):
                    raise ValueError(f"Protocol v{self.protocol_version} expects array format, got object")
                if len(parsed_data) != 5:
                    raise ValueError(f"Protocol v{self.protocol_version} expects 5-element array, got {len(parsed_data)}")
                
                message_dict = {
                    'topic': parsed_data[2],
                    'event': parsed_data[3], 
                    'ref': parsed_data[1],
                    'payload': parsed_data[4] or {}
                }
            else:
                # v1.0 expects object format: {"topic": ..., "event": ..., "ref": ..., "payload": ...}
                if not isinstance(parsed_data, dict):
                    raise ValueError(f"Protocol v{self.protocol_version} expects object format, got {type(parsed_data).__name__}")
                
                message_dict = parsed_data
            
            required_fields = ['topic', 'event', 'payload']
            for field in required_fields:
                if field not in message_dict:
                    raise ValueError(f"Missing required field '{field}'")
            
            return make_message(**message_dict)
            
        except Exception as e:
            self.logger.error(f'Failed to parse message {raw_message}: {e}')
            raise ValueError(f'Invalid message format: {e}') from e
    
    def serialize_message(self, message: ChannelMessage) -> str:
        self.logger.debug(f'Serializing message: {message}')
        
        try:
            if self.protocol_version == "2.0":
                # Official Phoenix Channels format: [join_ref, msg_ref, topic, event, payload]
                join_ref = message.ref or "1"
                msg_ref = message.ref or "1"
                message_array = [join_ref, msg_ref, message.topic, str(message.event), message.payload]
                serialized_bytes = json_handler.dumps(message_array)
                serialized = serialized_bytes.decode('utf-8')
            else:
                serialized_bytes = json_handler.dumps(message)
                serialized = serialized_bytes.decode('utf-8')
            
            self.logger.debug(f'Serialized to: {serialized}')
            return serialized
            
        except Exception as e:
            self.logger.error(f'Failed to serialize message {message}: {e}')
            raise TypeError(f'Cannot serialize message: {e}') from e
    
    def get_protocol_version(self) -> str:
        return self.protocol_version
    
    def set_protocol_version(self, version: str) -> None:
        self.logger.info(f"Changing protocol version from {self.protocol_version} to {version}")
        self.protocol_version = version
