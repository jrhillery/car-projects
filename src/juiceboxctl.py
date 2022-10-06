
import logging
from argparse import ArgumentParser, Namespace

import sys

from juicebox.jbdetails import JbDetails
from juicebox.jbinterface import JbInterface, JuiceBoxException
from util.configure import Configure


class JuiceBoxCtl(object):
    """Controls JuiceBox devices"""

    def __init__(self, args: Namespace | None = None):
        self.specifiedJuiceBoxName: str | None = None if args is None else args.juiceBoxName
        self.specifiedMaxAmps: int | None = None if args is None else args.maxAmps
        self.jbIntrfc = JbInterface()

        if self.specifiedMaxAmps is not None:
            if self.specifiedMaxAmps < 0:
                self.specifiedMaxAmps = 0

            if self.specifiedMaxAmps > self.jbIntrfc.totalCurrent:
                self.specifiedMaxAmps = self.jbIntrfc.totalCurrent
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to set maximum JuiceBox charge currents",
                            epilog="Just displays status when no arguments are specified")
        ap.add_argument("juiceBoxName", nargs="?", metavar="name",
                        help="name prefix of JuiceBox to set (other gets remaining current)")
        ap.add_argument("-m", "--maxAmps", type=int, nargs="?", const=40, metavar="amps",
                        help="maximum current to set (Amps)")

        return ap.parse_args()
    # end parseArgs()

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        specifiedJuiceBox: JbDetails | None = None
        otherJuiceBox: JbDetails | None = None

        with self.jbIntrfc.session, self.jbIntrfc:
            self.jbIntrfc.logIn()
            juiceBoxes = self.jbIntrfc.getStateOfJuiceBoxes()

            for juiceBox in juiceBoxes:
                if not juiceBox.isOffline:
                    self.jbIntrfc.addMoreDetails(juiceBox)
                    logging.info(juiceBox.statusStr())

                    if self.specifiedJuiceBoxName:
                        if juiceBox.name.startswith(self.specifiedJuiceBoxName):
                            specifiedJuiceBox = juiceBox
                        else:
                            otherJuiceBox = juiceBox
            # end for

            if self.specifiedJuiceBoxName:
                if not specifiedJuiceBox or not otherJuiceBox:
                    raise JuiceBoxException(f"Unable to locate both JuiceBoxes,"
                                            f" found {[jb.name for jb in juiceBoxes]}")
                if self.specifiedMaxAmps is not None:
                    self.jbIntrfc.setNewMaximums(specifiedJuiceBox, self.specifiedMaxAmps,
                                                 otherJuiceBox)
        # end with
    # end main()

# end class JuiceBoxCtl


if __name__ == "__main__":
    clArgs = JuiceBoxCtl.parseArgs()
    Configure.logToFile()
    try:
        juiceCtl = JuiceBoxCtl(clArgs)
        juiceCtl.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug(f"{xcpt.__class__.__name__} suppressed:", exc_info=xcpt)
