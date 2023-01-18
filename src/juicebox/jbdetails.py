

class JbDetails(object):
    """Details of a JuiceBox"""

    # fields set in JbDetails.updateFromDict
    deviceId: str
    name: str
    loadGroupId: int | None
    isOffline: bool
    status: str
    maxCurrent: int

    # field set in JbInterface.addMoreDetails
    wireRating: int

    def __init__(self, juiceBoxState: dict):
        """Initialize this instance and allocate resources
        :param juiceBoxState: Dictionary of JuiceBox JSON data
        """
        self.updateFromDict(juiceBoxState)
    # end __init__(dict)

    def updateFromDict(self, juiceBoxState: dict) -> None:
        """Populate details of this JuiceBox
        :param juiceBoxState: Dictionary of JuiceBox JSON data
        """
        self.deviceId = juiceBoxState["unitID"]
        self.name = juiceBoxState["unitName"]
        self.loadGroupId = juiceBoxState["LoadGroupId"]
        self.isOffline = self.getOffline(juiceBoxState)
        self.status = juiceBoxState["StatusText"]
        self.maxCurrent = juiceBoxState["allowed_C"]
    # end updateFromDict(dict)

    @staticmethod
    def getOffline(juiceBoxState: dict) -> bool:
        """Return the is-offline value from the supplied JuiceBox state data
        :param juiceBoxState: Dictionary of JuiceBox JSON data
        """

        return juiceBoxState["IsOffline"]
    # end getOffline(dict)

    def pluggedIn(self) -> bool:
        """Return true when a car is plugged in to this JuiceBox"""

        return (not self.isOffline) and (self.status != "Available")
    # end pluggedIn()

    def limitToWireRating(self, maxCurrent: int) -> int:
        """Return a maximum current that does not exceed the wire rating of this JuiceBox
        :param maxCurrent: The desired maximum current
        :return: The maximum current that satisfies the wire rating of this JuiceBox
        """
        if hasattr(self, "wireRating"):

            if maxCurrent > self.wireRating:
                return self.wireRating

        return maxCurrent
    # end limitToWireRating(int)

    def statusStr(self) -> str:
        """Return a summary status suitable for display"""
        msg = f"{self.name} is {self.status}"

        if self.loadGroupId is None:
            msg += f" with maximum current {self.maxCurrent} A"
        else:
            msg += " with a load group controlling current"

        return msg
    # end statusStr()

    def __str__(self) -> str:
        return f"{self.name} id[{self.deviceId}]"
    # end __str__()

# end class JbDetails
