
from aiohttp import ClientResponse

from . import Interpret


class HTTPException(Exception):
    """Class for handled exceptions dealing with HTTP responses"""

    def __init__(self, msg: str, response: ClientResponse):
        super().__init__(msg)
        self.response = response
    # end __init__(str, Response | ClientResponse)

    @classmethod
    async def fromError(cls, badResponse: ClientResponse, target: str):
        """Factory method for bad async responses"""

        return cls(await Interpret.responseErr(badResponse, target), badResponse)
    # end fromError(ClientResponse, str)

    @classmethod
    async def fromXcp(cls, xcption: BaseException, resp: ClientResponse, target: str):
        """Factory method for async Exceptions"""

        return cls(await Interpret.responseXcp(resp, xcption, target), resp)
    # end fromXcp(BaseException, ClientResponse, str)

# end class HTTPException
