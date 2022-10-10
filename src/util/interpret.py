
from threading import current_thread

from requests import Response

from util.extresponse import ExtResponse


class Interpret(object):
    @staticmethod
    def responseErr(resp: ExtResponse) -> str:

        return (f"{resp.status_code} {resp.decodeReason()} in {current_thread().name}:"
                f" {resp.text} for url {resp.url}")
    # end responseErr(Response)

    @staticmethod
    def responseXcp(resp: Response, xcp: BaseException) -> str:

        return (f"Exception {xcp.__class__.__name__}: {str(xcp)} in {current_thread().name}:"
                f" {resp.text} for url {resp.url}")
    # end responseXcp(Response, BaseException)

# end class Interpret
