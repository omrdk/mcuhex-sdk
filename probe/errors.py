class ProbeError(Exception):
    """Probe-level error with a structured error code.

    The error_code string is sent directly on the WebSocket response
    and must match a ConnectionErrorCode enum member in the web client.
    """
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code
