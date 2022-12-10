
import asyncio
import json
import logging
import sys
from argparse import ArgumentParser, Namespace
from contextlib import aclosing

from juicebox import JbDetails, JbInterface
from util import Configure, ExceptionGroupHandler


class JuiceBoxCtl(object):
    """Controls JuiceBox devices"""

    def __init__(self, args: Namespace | None = None):
        self.specifiedMaxAmps: int | None = args.maxAmps
        self.specifiedJuiceBoxName: str | None = args.juiceBoxName

        if self.specifiedMaxAmps is not None and self.specifiedJuiceBoxName is None:
            logging.error("Missing required JuiceBox name prefix when specifying max current")
            sys.exit(2)

        if self.specifiedMaxAmps is not None:
            if self.specifiedMaxAmps < 0:
                self.specifiedMaxAmps = 0

        with open(Configure.findParmPath().joinpath("carjuiceboxmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            carJuiceBoxMapping: dict = json.load(mappingFile)

        self.minPluggedCurrent: int = carJuiceBoxMapping["minPluggedCurrent"]
        self.totalCurrent: int = carJuiceBoxMapping["totalCurrent"]
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to set maximum JuiceBox charge currents")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-m", "--maxAmps", type=int, nargs="?", const=99, metavar="amps",
                           help="maximum current to set (Amps)")
        ap.add_argument("juiceBoxName", nargs="?", metavar="name",
                        help="name prefix of JuiceBox to set (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    async def specifyMaxCurrent(self, jbIntrfc: JbInterface,
                                juiceBoxes: list[JbDetails]) -> None:
        """Set the specified JuiceBox maximum current to a given value
           (the other JuiceBox gets remaining current)"""
        specifiedJuiceBox: JbDetails | None = None
        otherJuiceBox: JbDetails | None = None

        for juiceBox in juiceBoxes:
            if juiceBox.name.startswith(self.specifiedJuiceBoxName):
                specifiedJuiceBox = juiceBox
            else:
                otherJuiceBox = juiceBox
        # end for

        if not specifiedJuiceBox:
            raise Exception(
                f"Unable to locate JuiceBox starting with {self.specifiedJuiceBoxName},"
                f" found {[jb.name for jb in juiceBoxes]}")
        if not otherJuiceBox:
            raise Exception(f"Unable to locate both JuiceBoxes,"
                            f" found {[jb.name for jb in juiceBoxes]}")

        await jbIntrfc.setNewMaximums(specifiedJuiceBox, self.specifiedMaxAmps, otherJuiceBox)
    # end specifyMaxCurrent(JbInterface, list[JbDetails])

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        async with aclosing(JbInterface(self.minPluggedCurrent,
                                        self.totalCurrent)) as jbIntrfc:
            jbIntrfc: JbInterface
            await jbIntrfc.logIn()
            juiceBoxes = await jbIntrfc.getStateOfJuiceBoxes()

            match True:
                case _ if self.specifiedMaxAmps is not None:
                    await self.specifyMaxCurrent(jbIntrfc, juiceBoxes)
            # end match
        # end async with (jbIntrfc is closed)
    # end main()

# end class JuiceBoxCtl


if __name__ == "__main__":
    clArgs = JuiceBoxCtl.parseArgs()
    Configure.logToFile()
    try:
        juiceCtl = JuiceBoxCtl(clArgs)
        asyncio.run(juiceCtl.main())
    except Exception as xcption:
        for xcpt in ExceptionGroupHandler.iterGroup(xcption):
            logging.error(xcpt)
            logging.debug(f"{xcpt.__class__.__name__} suppressed:", exc_info=xcpt)
