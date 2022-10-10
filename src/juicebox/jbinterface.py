
import json
import logging
from collections.abc import ValuesView
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Type

from pyquery import PyQuery
from requests import HTTPError, Response, Session

from util.configure import Configure
from util.interpret import Interpret
from .jbdetails import JbDetails


class JbException(HTTPError):
    """Class for handled exceptions"""

    @classmethod
    def fromError(cls, badResponse: Response):
        """Factory method for bad responses"""

        return cls(Interpret.responseErr(badResponse), response=badResponse)
    # end fromError(Response)

    @classmethod
    def fromXcp(cls, xcption: BaseException, badResponse: Response):
        """Factory method for Exceptions"""

        return cls(Interpret.responseXcp(badResponse, xcption), response=badResponse)
    # end fromXcp(BaseException, Response)

# end class JbException


class JbInterface(AbstractContextManager["JbInterface"]):
    """Provides an interface to authorized JuiceBox devices"""
    XHR_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, totalCurrent: int):
        self.totalCurrent: int = totalCurrent
        self.session = Session()
        self.loToken: str | None = None

        with open(Configure.findParmPath().joinpath("juicenetlogincreds.json"),
                  "r", encoding="utf-8") as credFile:
            self.loginCreds: dict = json.load(credFile)

        # provide another default request header
        self.session.headers.update({"Accept-Language": "en-US,en;q=0.9"})
    # end __init__(int)

    def logIn(self) -> None:
        """Log-in to JuiceNet"""
        url = "https://home.juice.net/Account/Login"

        resp = self.session.request("GET", url)

        if resp.status_code != 200:
            raise JbException.fromError(resp)

        try:
            liToken = PyQuery(resp.text).find(
                "form.form-vertical > input[name='__RequestVerificationToken']").attr("value")
        except Exception as e:
            raise JbException.fromXcp(e, resp) from e

        headers = {"Cache-Control": "max-age=0"}
        data = {
            "__RequestVerificationToken": liToken,
            "Email": self.loginCreds["email"],
            "Password": self.loginCreds["password"],
            "IsGreenButtonAuth": "False",
            "RememberMe": "false",
        }
        resp = self.session.request("POST", url, headers=headers, data=data)

        if resp.status_code != 200:
            raise JbException.fromError(resp)

        try:
            self.loToken = PyQuery(resp.text).find(
                "form#logoutForm > input[name='__RequestVerificationToken']").attr("value")
        except Exception as e:
            raise JbException.fromXcp(e, resp) from e
    # end logIn()

    def logOut(self) -> None:
        """Log-out from JuiceNet"""
        url = "https://home.juice.net/Account/LogOff"
        headers = {"Cache-Control": "max-age=0"}
        data = {"__RequestVerificationToken": self.loToken}

        resp = self.session.request("POST", url, headers=headers, data=data)
        self.loToken = None

        if resp.status_code != 200:
            raise JbException.fromError(resp)
    # end logOut()

    def getStateOfJuiceBoxes(self) -> list[JbDetails]:
        """Get all active JuiceBoxes and their latest states."""
        url = "https://home.juice.net/Portal/GetUserUnitsJson"
        data = {"__RequestVerificationToken": self.loToken}

        resp = self.session.request("POST", url, headers=JbInterface.XHR_HEADERS, data=data)

        if resp.status_code == 200:
            try:
                juiceBoxStates: ValuesView[dict] = resp.json()["Units"].values()
            except Exception as e:
                raise JbException.fromXcp(e, resp) from e

            return [self.addMoreDetails(JbDetails(jbState)) for jbState in juiceBoxStates]
        else:
            raise JbException.fromError(resp)
    # end getStateOfJuiceBoxes()

    def addMoreDetails(self, juiceBox: JbDetails) -> JbDetails:
        url = "https://home.juice.net/Portal/Details"
        params = {"unitID": juiceBox.deviceId}

        resp = self.session.request("GET", url, params=params)

        if resp.status_code == 200:
            try:
                wireRatingElement = PyQuery(resp.text).find("input#wire_rating")
                juiceBox.wireRating = int(wireRatingElement.attr("value"))
            except Exception as e:
                raise JbException.fromXcp(e, resp) from e
        else:
            raise JbException.fromError(resp)

        return juiceBox
    # end addMoreDetails(JbDetails)

    def setMaxCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> None:
        # JuiceBox won't accept max of 0, so use 1 instead
        if maxCurrent < 1:
            maxCurrent = 1
        maxCurrent = juiceBox.limitToWireRating(maxCurrent)

        url = "https://home.juice.net/Portal/SetLimit"
        data = {
            "__RequestVerificationToken": self.loToken,
            "unitID": juiceBox.deviceId,
            "allowedC": maxCurrent,
        }
        resp = self.session.request("POST", url, headers=JbInterface.XHR_HEADERS, data=data)

        if resp.status_code != 200:
            raise JbException.fromError(resp)

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
        maxAmpsA = juiceBoxA.limitToWireRating(maxAmpsA)
        maxAmpsB = juiceBoxB.limitToWireRating(self.totalCurrent - maxAmpsA)
        maxAmpsA = self.totalCurrent - maxAmpsB

        if maxAmpsA < juiceBoxA.maxCurrent:
            # decreasing juiceBoxA limit, so do it first
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
            self.setMaxCurrent(juiceBoxB, maxAmpsB)
        else:
            self.setMaxCurrent(juiceBoxB, maxAmpsB)
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
    # end setNewMaximums(JbDetails, int, JbDetails)

    def __exit__(self, exc_type: Type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool | None:

        if self.loToken:
            self.logOut()

        return None
    # end __exit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

# end class JbInterface
