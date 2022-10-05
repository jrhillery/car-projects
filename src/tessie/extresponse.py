
from threading import current_thread

from requests import Response


class ExtResponse(Response):
    """Extend Response object"""

    def __init__(self, orig: Response):
        super().__init__()
        # noinspection PyProtectedMember
        self._content = orig._content
        self.status_code = orig.status_code
        self.headers = orig.headers
        self.raw = orig.raw
        self.url = orig.url
        self.encoding = orig.encoding
        self.history = orig.history
        self.reason = orig.reason
        self.cookies = orig.cookies
        self.elapsed = orig.elapsed
        self.request = orig.request
    # end __init__(Response)

    def unknownSummary(self) -> str:
        return (f"{self.status_code} {self.decodeReason()} in {current_thread().name}:"
                f" {self.text} for url {self.url}")
    # end unknownSummary()

    def decodeReason(self) -> str:
        reason = ExtResponse.decodeText(self.reason)

        if not reason:
            reason = "Error"

        return reason
    # end decodeReason()

    @staticmethod
    def decodeText(text: bytes | str) -> str:
        if isinstance(text, bytes):
            # Some servers choose to localize their reason strings.
            try:
                string = text.decode("utf-8")
            except UnicodeDecodeError:
                string = text.decode("iso-8859-1")
        else:
            string = text

        return string
    # end decodeText(bytes | str)

# end class ExtResponse
