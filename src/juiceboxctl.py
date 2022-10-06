
import json
import logging
from argparse import ArgumentParser, Namespace
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Type

import sys
from pyquery import PyQuery
from requests import HTTPError, Session

from util.configure import Configure
from util.extresponse import ExtResponse


class JuiceBoxException(HTTPError):
    """Class for handled exceptions"""

    @classmethod
    def fromError(cls, badResponse: ExtResponse):
        """Factory method for bad responses"""

        return cls(badResponse.unknownSummary(), response=badResponse)
    # end fromError(ExtResponse)

# end class JuiceBoxException


class JuiceBoxDetails(object):
    """Details of a JuiceBox"""

    deviceId: str
    name: str
    status: str
    maxCurrent: int

    def __init__(self, juiceBoxState: dict):
        self.updateFromDict(juiceBoxState)
    # end __init__(dict)

    def updateFromDict(self, juiceBoxState: dict) -> None:
        self.deviceId = juiceBoxState["unitID"]
        self.name = juiceBoxState["unitName"]
        self.status = juiceBoxState["StatusText"]
        self.maxCurrent = juiceBoxState["allowed_C"]
    # end updateFromDict(dict)

    def statusStr(self) -> str:
        return (f"{self.name} is {self.status}"
                f" with maximum current {self.maxCurrent} A")
    # end statusStr()

    def __str__(self) -> str:
        return f"{self.name} id[{self.deviceId}]"
    # end __str__()

# end class JuiceBoxDetails


class JuiceBoxCtl(AbstractContextManager["JuiceBoxCtl"]):
    """Controls JuiceBox devices"""

    def __init__(self, args: Namespace | None = None):
        self.specifiedJuiceBoxName: str | None = None if args is None else args.juiceBoxName
        self.specifiedMaxAmps: int | None = None if args is None else args.maxAmps
        self.session = Session()
        self.loToken: str | None = None

        with open(Configure.findParmPath().joinpath("juicenetlogincreds.json"),
                  "r", encoding="utf-8") as credFile:
            self.loginCreds: dict = json.load(credFile)

        self.totalCurrent: int = self.loginCreds["totalCurrent"]

        if self.specifiedMaxAmps is not None:
            if self.specifiedMaxAmps < 0:
                self.specifiedMaxAmps = 0

            if self.specifiedMaxAmps > self.totalCurrent:
                self.specifiedMaxAmps = self.totalCurrent
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to set maximum JuiceBox charge currents",
                            epilog="Just displays status when no arguments are specified")
        ap.add_argument("juiceBoxName", nargs="?", metavar="name",
                        help="name prefix of JuiceBox to set (other gets remaining current)")
        ap.add_argument("-a", "--maxAmps", type=int, metavar="amps",
                        help="maximum current to set (Amps)")

        return ap.parse_args()
    # end parseArgs()

    def logIn(self) -> None:
        """Log-in to JuiceNet"""
        url = "https://home.juice.net/Account/Login"
        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'sec-ch-ua': '"Google Chrome";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }

        resp = ExtResponse(self.session.request("GET", url, headers=headers))

        if resp.status_code != 200:
            raise JuiceBoxException.fromError(resp)

        liToken = PyQuery(resp.text).find(
            "form.form-vertical > input[name='__RequestVerificationToken']")

        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
            'Origin': 'https://home.juice.net',
            'Referer': 'https://home.juice.net/Account/Login',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'sec-ch-ua': '"Google Chrome";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        data = {
            '__RequestVerificationToken': liToken.attr("value"),
            'Email': self.loginCreds["email"],
            'Password': self.loginCreds["password"],
            'IsGreenButtonAuth': 'False',
            'RememberMe': 'false',
        }

        resp = ExtResponse(self.session.request("POST", url, headers=headers, data=data))

        if resp.status_code != 200:
            raise JuiceBoxException.fromError(resp)

        self.loToken = PyQuery(resp.text).find(
            "form#logoutForm > input[name='__RequestVerificationToken']").attr("value")
    # end logIn()

    def logOut(self) -> None:
        """Log-out from JuiceNet"""
        url = 'https://home.juice.net/Account/LogOff'
        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'max-age=0',
            'Origin': 'https://home.juice.net',
            'Referer': 'https://home.juice.net/Portal',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'sec-ch-ua': '"Google Chrome";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        data = {
            '__RequestVerificationToken': self.loToken,
        }

        resp = ExtResponse(self.session.request("POST", url, headers=headers, data=data))
        self.loToken = None

        if resp.status_code != 200:
            raise JuiceBoxException.fromError(resp)
    # end logOut()

    def getStateOfJuiceBoxes(self) -> list[JuiceBoxDetails]:
        """Get all active JuiceBoxes and their latest states."""
        url = 'https://home.juice.net/Portal/GetUserUnitsJson'
        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://home.juice.net',
            'Referer': 'https://home.juice.net/Portal',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua': '"Google Chrome";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        data = {
            '__RequestVerificationToken': self.loToken,
        }

        resp = ExtResponse(self.session.request("POST", url, headers=headers, data=data))

        if resp.status_code == 200:
            juiceBoxStates: list[dict] = resp.json()["Units"].values()
            juiceBoxes = []

            for juiceBoxState in juiceBoxStates:
                juiceBoxes.append(JuiceBoxDetails(juiceBoxState))

            return juiceBoxes
        else:
            raise JuiceBoxException.fromError(resp)
    # end getStateOfJuiceBoxes()

    def setMaxCurrent(self, juiceBox: JuiceBoxDetails, maxCurrent: int) -> None:
        # JuiceBox won't accept max of 0, so use 1 instead
        if maxCurrent < 1:
            maxCurrent = 1

        url = 'https://home.juice.net/Portal/SetLimit'
        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://home.juice.net',
            'Referer': 'https://home.juice.net/Portal/Details',
            'Request-Context': 'appId=cid-v1:72309e3b-8111-49c2-afbd-2dbe2d97b3c2',
            'Request-Id': '|cnzEj.TYDgf',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua': '"Google Chrome";v="105", "Not)A;Brand";v="8", "Chromium";v="105"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        data = {
            '__RequestVerificationToken': self.loToken,
            'unitID': juiceBox.deviceId,
            'allowedC': maxCurrent,
        }
        resp = ExtResponse(self.session.request("POST", url, headers=headers, data=data))

        if resp.status_code != 200:
            raise JuiceBoxException.fromError(resp)

        logging.info(f"{juiceBox.name} maximum current changed"
                     f" from {juiceBox.maxCurrent} to {maxCurrent} A")
        juiceBox.maxCurrent = maxCurrent
    # end setMaxCurrent(JuiceBoxDetails, int)

    def setNewMaximums(self, juiceBoxA: JuiceBoxDetails, maxAmpsA: int,
                       juiceBoxB: JuiceBoxDetails) -> None:
        """Set JuiceBox maximum currents, decrease one before increasing the other

        :param juiceBoxA: One of the JuiceBoxes to set
        :param maxAmpsA: The desired maximum current for juiceBoxA
        :param juiceBoxB: The other JuiceBox to set (gets remaining current)
        """
        if maxAmpsA < juiceBoxA.maxCurrent:
            # decreasing juiceBoxA limit, so do it first
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
            self.setMaxCurrent(juiceBoxB, self.totalCurrent - maxAmpsA)
        else:
            self.setMaxCurrent(juiceBoxB, self.totalCurrent - maxAmpsA)
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
    # end setNewMaximums(JuiceBoxDetails, int, JuiceBoxDetails)

    def __exit__(self, exc_type: Type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool | None:

        if self.loToken:
            self.logOut()

        return None
    # end __exit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        specifiedJuiceBox: JuiceBoxDetails | None = None
        otherJuiceBox: JuiceBoxDetails | None = None

        with self.session, self:
            self.logIn()
            juiceBoxes = self.getStateOfJuiceBoxes()

            for juiceBox in juiceBoxes:
                if not juiceBox.status.startswith("Offline"):
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
                    self.setNewMaximums(specifiedJuiceBox, self.specifiedMaxAmps,
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
