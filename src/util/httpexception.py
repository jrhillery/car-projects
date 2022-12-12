
from typing import Self

from aiohttp import ClientResponse

from . import Interpret


class HTTPException(Exception):
    """Class for handled exceptions dealing with HTTP responses"""

    def __init__(self, msg: str, response: ClientResponse):
        """Initialize this instance and allocate resources"""
        super().__init__(msg)
        self.response = response
    # end __init__(str, ClientResponse)

    @classmethod
    async def fromError(cls, badResponse: ClientResponse, target: str) -> Self:
        """Factory method for bad async responses
        :param badResponse: Response from an HTTP request
        :param target: What we are attempting to access
        :return: An HTTPException instance
        """

        return cls(await Interpret.responseErr(badResponse, target), badResponse)
    # end fromError(ClientResponse, str)

    @classmethod
    async def fromXcp(cls, xcption: BaseException, resp: ClientResponse, target: str) -> Self:
        """Factory method for async Exceptions
        :param xcption: The exception encountered
        :param resp: The corresponding response from an HTTP request
        :param target: What we are attempting to access
        :return: An HTTPException instance
        """

        return cls(await Interpret.responseXcp(resp, xcption, target), resp)
    # end fromXcp(BaseException, ClientResponse, str)

# end class HTTPException
