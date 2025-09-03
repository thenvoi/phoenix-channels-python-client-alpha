import json
import pytest_asyncio
import websockets
from websockets import serve
from typing import AsyncGenerator


class FakePhoenixServer:
    def __init__(self, host="localhost", port=8765):
        self.host = host
        self.port = port
        self.server = None
        self.client_websocket = None
        self.valid_topics = {
            "test-topic",
        }
        
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
    
    def is_valid_topic(self, topic):
        """Check if a topic is valid/legal"""
        return topic in self.valid_topics
    
    async def handle_message(self, data):
        """Handle incoming messages and send appropriate replies"""
        topic = data.get("topic")
        event = data.get("event")
        ref = data.get("ref")
        
        if event == "phx_join":
            # Check if topic is valid before allowing join
            if self.is_valid_topic(topic):
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
    
    async def simulate_server_event(self, topic, event, payload):
        """Simulate a server event being sent to the client for testing purposes"""
        if self.client_websocket:
            message = {
                "topic": topic,
                "event": event,
                "ref": None,
                "payload": payload
            }
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
async def phoenix_server() -> AsyncGenerator[FakePhoenixServer, None]:
    """Fixture that provides a fake Phoenix WebSocket server"""
    server = FakePhoenixServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()



