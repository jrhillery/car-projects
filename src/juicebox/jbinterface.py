
import json
import logging
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Type

from pyquery import PyQuery
from requests import HTTPError, Session

from util.configure import Configure
from util.extresponse import ExtResponse
from .jbdetails import JbDetails


class JuiceBoxException(HTTPError):
    """Class for handled exceptions"""

    @classmethod
    def fromError(cls, badResponse: ExtResponse):
        """Factory method for bad responses"""

        return cls(badResponse.unknownSummary(), response=badResponse)
    # end fromError(ExtResponse)

# end class JuiceBoxException


class JbInterface(AbstractContextManager["JbInterface"]):
    """Provides an interface to authorized JuiceBox devices"""

    def __init__(self):
        self.session = Session()
        self.loToken: str | None = None

        with open(Configure.findParmPath().joinpath("juicenetlogincreds.json"),
                  "r", encoding="utf-8") as credFile:
            self.loginCreds: dict = json.load(credFile)

        self.totalCurrent: int = self.loginCreds["totalCurrent"]
    # end __init__()

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

    def getStateOfJuiceBoxes(self) -> list[JbDetails]:
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

            return [JbDetails(jbState) for jbState in juiceBoxStates]
        else:
            raise JuiceBoxException.fromError(resp)
    # end getStateOfJuiceBoxes()

    def setMaxCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> None:
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
    # end setMaxCurrent(JbDetails, int)

    def setNewMaximums(self, juiceBoxA: JbDetails, maxAmpsA: int,
                       juiceBoxB: JbDetails) -> None:
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
    # end setNewMaximums(JbDetails, int, JbDetails)

    def __exit__(self, exc_type: Type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool | None:

        if self.loToken:
            self.logOut()

        return None
    # end __exit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

# end class JbInterface
