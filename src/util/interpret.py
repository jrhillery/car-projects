
from aiohttp import ClientResponse
from requests import Response


class AInterpret(object):
    @staticmethod
    async def responseErr(resp: ClientResponse, target: str) -> str:
        return (f"{resp.status} {Interpret.decodeReason(resp)}"
                f" accessing {target}: {await resp.text()} for url {resp.url}")
    # end responseErr(ClientResponse, str)

    @staticmethod
    async def responseXcp(resp: ClientResponse, xcp: BaseException, target: str) -> str:
        return (f"Exception {xcp.__class__.__name__}: {str(xcp)}"
                f" accessing {target}: {await resp.text()} for url {resp.url}")
    # end responseXcp(ClientResponse, BaseException, str)

    @staticmethod
    def decodeReason(resp: ClientResponse) -> str:
        return Interpret.decodeReason(resp)
    # end decodeReason(ClientResponse)

# end class AInterpret


class Interpret(object):
    @staticmethod
    def responseErr(resp: Response, target: str) -> str:
        return (f"{resp.status_code} {Interpret.decodeReason(resp)}"
                f" accessing {target}: {resp.text} for url {resp.url}")
    # end responseErr(Response, str)

    @staticmethod
    def responseXcp(resp: Response, xcp: BaseException, target: str) -> str:
        return (f"Exception {xcp.__class__.__name__}: {str(xcp)}"
                f" accessing {target}: {resp.text} for url {resp.url}")
    # end responseXcp(Response, BaseException, str)

    @staticmethod
    def decodeReason(resp: Response | ClientResponse) -> str:
        reason = Interpret.decodeText(resp.reason)

        if not reason:
            reason = "Error"

        return reason
    # end decodeReason(Response | ClientResponse)

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
