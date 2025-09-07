import json
import pytest_asyncio
import websockets
from websockets import serve
from typing import AsyncGenerator


class FakePhoenixServer:
    def __init__(self, host="localhost", port=8765, protocol_version="v1"):
        self.host = host
        self.port = port
        self.protocol_version = protocol_version
        self.server = None
        self.client_websocket = None
        self.valid_topics = {
            "test-topic",
            "test-topic-b",
        }
        # Track subscribed topics and their join_refs
        self.subscribed_topics = {}  # topic_name -> join_ref
        
    async def handler(self, websocket):
        """Handle WebSocket connections and messages"""
        self.client_websocket = websocket
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.handle_message(data)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.client_websocket = None
            # Clear subscriptions when connection is closed
            self.subscribed_topics.clear()
    
    def is_valid_topic(self, topic):
        """Check if a topic is valid/legal"""
        return topic in self.valid_topics
    
    async def handle_message(self, data):
        """Handle incoming messages and send appropriate replies"""
        if self.protocol_version == "v1":
            await self.handle_message_v1_protocol(data)
        elif self.protocol_version == "v2":
            await self.handle_message_v2_protocol(data)
        else:
            raise ValueError(f"Unsupported protocol version: {self.protocol_version}")
    
    async def handle_message_v1_protocol(self, data):
        """Handle incoming v1 protocol messages (JSON object format)"""
        topic = data.get("topic")
        event = data.get("event")
        ref = data.get("ref")
        
        if event == "phx_join":
            # Check if topic is valid before allowing join
            if self.is_valid_topic(topic):
                # Track the subscription with join_ref (in v1, join_ref is same as ref)
                self.subscribed_topics[topic] = ref
                # Send successful join reply for valid topics
                reply = {
                    "topic": topic,
                    "event": "phx_reply",
                    "ref": ref,
                    "payload": {"status": "ok", "response": {}}
                }
            else:
                # Send error reply for invalid topics
                reply = {
                    "topic": topic,
                    "event": "phx_reply",
                    "ref": ref,
                    "payload": {"status": "error", "response": {"reason": "unmatched topic"}}
                }
            await self.client_websocket.send(json.dumps(reply))
        elif event == "phx_leave":
            # Send successful leave reply
            reply = {
                "topic": topic,
                "event": "phx_reply", 
                "ref": ref,
                "payload": {"status": "ok", "response": {}}
            }
            await self.client_websocket.send(json.dumps(reply))
            
            # Remove from subscribed topics
            if topic in self.subscribed_topics:
                del self.subscribed_topics[topic]
    
    async def handle_message_v2_protocol(self, data):
        """Handle incoming v2 protocol messages (array format: [join_ref, msg_ref, topic, event, payload])"""
        if not isinstance(data, list) or len(data) != 5:
            return  # Invalid v2 message format
        
        join_ref, msg_ref, topic, event, payload = data
        
        if event == "phx_join":
            # Check if topic is valid before allowing join
            if self.is_valid_topic(topic):
                # Track the subscription with join_ref
                self.subscribed_topics[topic] = join_ref
                # Send successful join reply for valid topics
                reply = [join_ref, msg_ref, topic, "phx_reply", {"status": "ok", "response": {}}]
            else:
                # Send error reply for invalid topics
                reply = [join_ref, msg_ref, topic, "phx_reply", {"status": "error", "response": {"reason": "unmatched topic"}}]
            await self.client_websocket.send(json.dumps(reply))
        elif event == "phx_leave":
            # Send successful leave reply
            reply = [join_ref, msg_ref, topic, "phx_reply", {"status": "ok", "response": {}}]
            await self.client_websocket.send(json.dumps(reply))
            
            # Also send phx_close message after successful leave
            close_message = [join_ref, join_ref, topic, "phx_close", {}]
            await self.client_websocket.send(json.dumps(close_message))
            
            # Remove from subscribed topics
            if topic in self.subscribed_topics:
                del self.subscribed_topics[topic]
    
    async def simulate_server_event(self, topic, event, payload):
        """Simulate a server event being sent to the client for testing purposes"""
        if self.protocol_version == "v1":
            await self.simulate_server_event_v1_protocol(topic, event, payload)
        elif self.protocol_version == "v2":
            await self.simulate_server_event_v2_protocol(topic, event, payload)
        else:
            raise ValueError(f"Unsupported protocol version: {self.protocol_version}")
    
    async def simulate_server_event_v1_protocol(self, topic, event, payload):
        """Simulate a server event for v1 protocol (JSON object format)"""
        if self.client_websocket and topic in self.subscribed_topics:
            message = {
                "topic": topic,
                "event": event,
                "ref": None,
                "payload": payload
            }
            await self.client_websocket.send(json.dumps(message))
    
    async def simulate_server_event_v2_protocol(self, topic, event, payload):
        """Simulate a server event for v2 protocol (array format)"""
        if self.client_websocket and topic in self.subscribed_topics:
            # Use the tracked join_ref for this topic
            join_ref = self.subscribed_topics[topic]
            message = [join_ref, None, topic, event, payload]
            await self.client_websocket.send(json.dumps(message))
    
    async def start(self):
        """Start the fake Phoenix server"""
        self.server = await serve(self.handler, self.host, self.port)
        
    async def stop(self):
        """Stop the fake Phoenix server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.client_websocket = None
    
    @property
    def url(self):
        return f"ws://{self.host}:{self.port}/socket/websocket"


@pytest_asyncio.fixture
async def phoenix_server_v1() -> AsyncGenerator[FakePhoenixServer, None]:
    """Fixture that provides a fake Phoenix WebSocket server (v1 protocol)"""
    server = FakePhoenixServer(protocol_version="v1")
    await server.start()
    try:
        yield server
    finally:
        await server.stop()

@pytest_asyncio.fixture
async def phoenix_server_v2() -> AsyncGenerator[FakePhoenixServer, None]:
    """Fixture that provides a fake Phoenix WebSocket server (v2 protocol)"""
    server = FakePhoenixServer(protocol_version="v2")
    await server.start()
    try:
        yield server
    finally:
        await server.stop()



