

class JbDetails(object):
    """Details of a JuiceBox"""

    deviceId: str
    name: str
    isOffline: bool
    status: str
    maxCurrent: int
    wireRating: int

    def __init__(self, juiceBoxState: dict):
        self.updateFromDict(juiceBoxState)
    # end __init__(dict)

    def updateFromDict(self, juiceBoxState: dict) -> None:
        self.deviceId = juiceBoxState["unitID"]
        self.name = juiceBoxState["unitName"]
        self.isOffline = juiceBoxState["IsOffline"]
        self.status = juiceBoxState["StatusText"]
        self.maxCurrent = juiceBoxState["allowed_C"]
    # end updateFromDict(dict)

    def limitToWireRating(self, maxCurrent: int) -> int:
        if hasattr(self, "wireRating"):

            if maxCurrent > self.wireRating:
                return self.wireRating

        return maxCurrent
    # end limitToWireRating(int)

    def statusStr(self) -> str:
        return (f"{self.name} is {self.status}"
                f" with maximum current {self.maxCurrent} A")
    # end statusStr()

    def __str__(self) -> str:
        return f"{self.name} id[{self.deviceId}]"
    # end __str__()

# end class JbDetails
