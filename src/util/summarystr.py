
from collections import UserString
from typing import Self


class SummaryStr(UserString):
    """Holds a summary status string suitable for display"""

    def __init__(self, content: object, updatedSinceLastSummary: bool):
        """Initialize this instance and allocate resources
        :param content: Summary status suitable for display
        :param updatedSinceLastSummary: True when data has been updated since last summarized
        """
        super().__init__(content)
        self.updatedSinceLastSummary = updatedSinceLastSummary
    # end __init__(object, bool)

    def __iadd__(self, suffix: object) -> Self:
        """Support the += operator
        :param suffix: Suffix to append as string
        """
        self.data += suffix

        return self
    # end __iadd__(object)

# end class SummaryStr
