
from collections.abc import Iterator


class ExceptionGroupHandler:

    @staticmethod
    def iterGroup(xcp: BaseException) -> Iterator[BaseException]:
        """Generate each exception when an exception group is supplied
        :param xcp: An exception to analyze, potentially an exception group
        :return: An iterator over the contained exceptions
        """
        if isinstance(xcp, BaseExceptionGroup):
            for subXcp in xcp.exceptions:
                yield subXcp
        else:
            yield xcp
    # end iterGroup(BaseException)

# end class ExceptionGroupHandler
