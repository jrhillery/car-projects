
import __main__ as main
import json
from pathlib import Path
from typing import Any

from util import Configure


class PersistentData(object):

    def __init__(self):
        """Initialize this instance and allocate resources"""
        self.needsSave = False
        try:
            with open(self.persistPath(), "r", encoding="utf-8") as persistFile:
                self._data: dict[str, dict] = json.load(persistFile)
        except FileNotFoundError:
            self._data: dict[str, dict] = {}
    # end __init__()

    def save(self) -> None:
        """Save this persistent data instance to file if needed"""
        if self.needsSave:
            with open(self.persistPath(), "w", encoding="utf-8") as persistFile:
                json.dump(self._data, persistFile, ensure_ascii=False, indent=3)
            self.needsSave = False
    # end save()

    @staticmethod
    def persistPath() -> Path:
        """Locate our persist file path
        :return: Path to our persist file
        """
        mainPath = Path(main.__file__)

        return Configure.findParmPath().joinpath(mainPath.stem + ".persist")
    # end persistPath()

    def setVal(self, category: str, instanceId: str, val: Any) -> None:
        """Store a value in this persistent data
        :param category: Classification given to this type of data
        :param instanceId: Identifier for this instance of data
        :param val: Data value
        """
        if category not in self._data:
            self._data[category] = {instanceId: val}
            self.needsSave = True
        else:
            cat = self._data[category]

            if instanceId not in cat or cat[instanceId] != val:
                cat[instanceId] = val
                self.needsSave = True
    # end setVal(str, str, Any)

    def getVal(self, category: str, instanceId: str) -> Any:
        """Retrieve a value from  this persistent data
        :param category: Classification given to this type of data
        :param instanceId: Identifier for this instance of data
        :return: Data value, if exists, otherwise None
        """
        try:
            return self._data[category][instanceId]
        except KeyError:
            return None
    # end getVal(str, str)

# end class PersistentData


if __name__ == "__main__":
    pd = PersistentData()
    bouncyToy: int = pd.getVal("bouncy", "j")

    if bouncyToy is None:
        bouncyToy = 646
        pd.setVal("bouncy", "j", bouncyToy)
        pd.setVal("clove", "s", 42)
    else:
        pd.setVal("bouncy", "s", 747)
        pd.setVal("bouncy", "j", 848)

    pd.save()
