
from pyquery import PyQuery


class LgDetails(object):
    """Details of a load group"""

    def __init__(self, loadGroupsPage: PyQuery):
        """Initialize this instance and allocate resources
        :param loadGroupsPage: query of load groups web page
        """
        loadGroupsTbl = loadGroupsPage.find("table#loadgroups-table")
        self.id = int(loadGroupsTbl.find("input.group-id").val())
        self.name: str = loadGroupsTbl.find("input.group-name").val()
        self.maxCurrent = int(loadGroupsTbl.find("input.group-max-current").val())
    # end __init__(PyQuery)

    def __str__(self) -> str:
        return f"{self.name} id[{self.id}]"
    # end __str__()

# end class LgDetails
