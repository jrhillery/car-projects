
from aiohttp import ClientResponse
from requests import Response

from . import AInterpret, Interpret


class HTTPException(Exception):
    """Class for handled exceptions dealing with HTTP responses"""

    def __init__(self, msg: str, response: Response | ClientResponse):
        super().__init__(msg)
        self.response = response
    # end __init__(str, Response | ClientResponse)

    @classmethod
    async def fromAsyncError(cls, badResponse: ClientResponse, target: str):
        """Factory method for bad async responses"""

        return cls(await AInterpret.responseErr(badResponse, target), badResponse)
    # end fromAsyncError(ClientResponse, str)

    @classmethod
    async def fromAsyncXcp(cls, xcption: BaseException, resp: ClientResponse, target: str):
        """Factory method for async Exceptions"""

        return cls(await AInterpret.responseXcp(resp, xcption, target), resp)
    # end fromAsyncXcp(BaseException, ClientResponse, str)

    @classmethod
    def fromError(cls, badResponse: Response, target: str):
        """Factory method for bad responses"""

        return cls(Interpret.responseErr(badResponse, target), badResponse)
    # end fromError(Response, str)

    @classmethod
    def fromXcp(cls, xcption: BaseException, resp: Response, target: str):
        """Factory method for Exceptions"""

        return cls(Interpret.responseXcp(resp, xcption, target), resp)
    # end fromXcp(BaseException, Response, str)

# end class HTTPException
