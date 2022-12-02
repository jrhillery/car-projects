
from asyncio import current_task

from aiohttp import ClientResponse


class Interpret(object):
    @staticmethod
    async def responseErr(resp: ClientResponse, target: str) -> str:
        return (f"{resp.status} {Interpret.decodeReason(resp)}"
                + await Interpret.responseContext(resp, target))
    # end responseErr(ClientResponse, str)

    @staticmethod
    async def responseXcp(resp: ClientResponse, xcp: BaseException, target: str) -> str:
        return (f"Exception {xcp.__class__.__name__}: {str(xcp)}"
                + await Interpret.responseContext(resp, target))
    # end responseXcp(ClientResponse, BaseException, str)

    @staticmethod
    async def responseContext(resp: ClientResponse, target: str) -> str:
        curTask = current_task()
        curTaskName = "" if curTask is None else f" in {curTask.get_name()}"
        try:
            # try to isolate an error message
            content = (await resp.json())['error']
        except Exception as e:
            # include the entire content body
            content = await resp.text()
            assert e is not None  # supress too broad exception clause warning

        return f" accessing {target}{curTaskName}: {content} for url {resp.url}"
    # end responseContext(ClientResponse, str)

    @staticmethod
    def decodeReason(resp: ClientResponse) -> str:
        reason = Interpret.decodeText(resp.reason)

        if not reason:
            reason = "Error"

        return reason
    # end decodeReason(ClientResponse)

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
