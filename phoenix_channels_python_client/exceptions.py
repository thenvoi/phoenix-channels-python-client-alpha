from phoenix_channels_python_client.phx_messages import Topic


class PHXClientError(Exception):
    pass


class PHXTopicTooManyRegistrationsError(PHXClientError):
    pass


class PHXConnectionError(PHXClientError):
    """Raised when there's an error connecting to the Phoenix WebSocket server."""
    pass


class TopicClosedError(PHXClientError):
    def __init__(self, topic: Topic, reason: str):
        self.topic = topic
        self.reason = reason
        super().__init__(topic, reason)
