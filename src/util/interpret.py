
import logging
from asyncio import gather, Task
from collections.abc import Sequence

from aiohttp import ClientResponse


class Interpret(object):
    @staticmethod
    async def waitForTasks(tasks: Sequence[Task]) -> None:
        """Wait for all of a given sequence of tasks to complete
        :param tasks: Tasks to wait for, all of which must return None or raise an exception
        """
        results = await gather(*tasks, return_exceptions=True)

        for task, res in zip(tasks, results):
            if res is not None:
                logging.error(f"Problem in task {task.get_name()}:"
                              f" Exception {res.__class__.__name__}:"
                              f" {str(res)}", exc_info=res)
    # end waitForTasks(Sequence[Task])

    @staticmethod
    async def responseErr(resp: ClientResponse, target: str) -> str:
        """Summarize an error in a given response object
        :param resp: Response from an HTTP request
        :param target: What we are attempting to access
        :return: Summary string
        """
        return (f"{resp.status} {Interpret.decodeReason(resp)}"
                + await Interpret.responseContext(resp, target))
    # end responseErr(ClientResponse, str)

    @staticmethod
    async def responseXcp(resp: ClientResponse, xcp: BaseException, target: str) -> str:
        """Summarize an exception related to a given response object
        :param resp: Response from an HTTP request
        :param xcp: Corresponding exception
        :param target: What we are attempting to access
        :return: Summary string
        """
        return (f"Exception {xcp.__class__.__name__}: {str(xcp)}"
                + await Interpret.responseContext(resp, target))
    # end responseXcp(ClientResponse, BaseException, str)

    @staticmethod
    async def responseContext(resp: ClientResponse, target: str) -> str:
        """Produce some context for a given response object
        :param resp: Response from an HTTP request
        :param target: What we are attempting to access
        :return: Context string
        """
        try:
            # try to isolate an error message
            content = (await resp.json())["error"]
        except Exception as e:
            # include the entire content body
            content = await resp.text()
            assert e is not None  # supress too broad exception clause warning

        return f" accessing {target}: {content} for url {resp.url}"
    # end responseContext(ClientResponse, str)

    @staticmethod
    def decodeReason(resp: ClientResponse) -> str:
        """Decode the response reason text
        :param resp: Response from an HTTP request
        :return: A string representing the response reason, or "Error" if no reason given
        """
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
