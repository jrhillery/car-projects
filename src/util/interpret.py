
from threading import current_thread

from requests import Response


class Interpret(object):
    @staticmethod
    def responseErr(resp: Response) -> str:

        return (f"{resp.status_code} {Interpret.decodeReason(resp)}"
                f" in {current_thread().name}: {resp.text} for url {resp.url}")
    # end responseErr(Response)

    @staticmethod
    def responseXcp(resp: Response, xcp: BaseException) -> str:

        return (f"Exception {xcp.__class__.__name__}: {str(xcp)}"
                f" in {current_thread().name}: {resp.text} for url {resp.url}")
    # end responseXcp(Response, BaseException)

    @staticmethod
    def decodeReason(resp: Response) -> str:
        reason = Interpret.decodeText(resp.reason)

        if not reason:
            reason = "Error"

        return reason
    # end decodeReason(Response)

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

# end class Interpret
