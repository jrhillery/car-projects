
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

# end class ExtResponse
