
from requests import HTTPError, Response

from . import Interpret


class HTTPException(HTTPError):
    """Class for handled exceptions dealing with HTTP responses"""

    @classmethod
    def fromError(cls, badResponse: Response):
        """Factory method for bad responses"""

        return cls(Interpret.responseErr(badResponse), response=badResponse)
    # end fromError(Response)

    @classmethod
    def fromXcp(cls, xcption: BaseException, resp: Response):
        """Factory method for Exceptions"""

        return cls(Interpret.responseXcp(resp, xcption), response=resp)
    # end fromXcp(BaseException, Response)

# end class HTTPException
