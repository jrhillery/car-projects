
import json
import logging

from pyquery import PyQuery
from requests import Response, Session

from util import Configure, HTTPException
from . import JbDetails


class JbInterface(object):
    """Provide an interface to authorized JuiceBox devices"""
    XHR_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, minPluggedCurrent: int, totalCurrent: int):
        self.minPluggedCurrent: int = minPluggedCurrent
        self.totalCurrent: int = totalCurrent
        self.session = Session()
        self.loToken: str | None = None

        # provide another default request header
        self.session.headers.update({"Accept-Language": "en-US,en;q=0.9"})
    # end __init__(int, int)

    @staticmethod
    def logInBody(resp: Response) -> dict[str, str]:
        """Return a log-in post request body"""
        try:
            liToken: str = PyQuery(resp.text).find(
                "form.form-vertical > input[name='__RequestVerificationToken']").val()
        except Exception as e:
            raise HTTPException.fromXcp(e, resp) from e

        with open(Configure.findParmPath().joinpath("juicenetlogincreds.json"),
                  "r", encoding="utf-8") as credFile:
            loginCreds: dict[str, str] = json.load(credFile)

        return {
            "__RequestVerificationToken": liToken,
            "Email": loginCreds["email"],
            "Password": loginCreds["password"],
            "IsGreenButtonAuth": "False",
            "RememberMe": "false",
        }
    # end logInBody(Response)

    def logIn(self) -> None:
        """Log-in to JuiceNet"""
        url = "https://home.juice.net/Account/Login"

        resp = self.session.request("GET", url)

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

        headers = {"Cache-Control": "max-age=0"}
        resp = self.session.request("POST", url, headers=headers, data=self.logInBody(resp))

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

        try:
            self.loToken = PyQuery(resp.text).find(
                "form#logoutForm > input[name='__RequestVerificationToken']").val()
        except Exception as e:
            raise HTTPException.fromXcp(e, resp) from e
    # end logIn()

    def logOut(self) -> None:
        """Log-out from JuiceNet"""
        url = "https://home.juice.net/Account/LogOff"
        headers = {"Cache-Control": "max-age=0"}
        body = {"__RequestVerificationToken": self.loToken}

        resp = self.session.request("POST", url, headers=headers, data=body)
        self.loToken = None

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)
    # end logOut()

    def getStateOfJuiceBoxes(self) -> list[JbDetails]:
        """Get all active JuiceBoxes and their latest states."""
        url = "https://home.juice.net/Portal/GetUserUnitsJson"
        body = {"__RequestVerificationToken": self.loToken}

        resp = self.session.request("POST", url, headers=JbInterface.XHR_HEADERS, data=body)

        if resp.status_code == 200:
            try:
                unitMap: dict[str, dict] = resp.json()["Units"]
                juiceBoxStates = unitMap.values()
            except Exception as e:
                raise HTTPException.fromXcp(e, resp) from e

            return [self.addMoreDetails(JbDetails(jbState)) for jbState in juiceBoxStates]
        else:
            raise HTTPException.fromError(resp)
    # end getStateOfJuiceBoxes()

    def addMoreDetails(self, juiceBox: JbDetails) -> JbDetails:
        """Augment details of the specified JuiceBox"""
        url = "https://home.juice.net/Portal/Details"
        qryParms = {"unitID": juiceBox.deviceId}

        resp = self.session.request("GET", url, params=qryParms)

        if resp.status_code == 200:
            try:
                pQry = PyQuery(resp.text)
                juiceBox.wireRating = int(pQry.find("input#wire_rating").val())
            except Exception as e:
                raise HTTPException.fromXcp(e, resp) from e
        else:
            raise HTTPException.fromError(resp)

        return juiceBox
    # end addMoreDetails(JbDetails)

    def setMaxCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> None:
        """Set the JuiceBox maximum current as close as possible to a specified maximum"""
        # JuiceBox doesn't accept max of 0, so use 1 instead
        if maxCurrent < 1:
            maxCurrent = 1
        maxCurrent = juiceBox.limitToWireRating(maxCurrent)

        if maxCurrent != juiceBox.maxCurrent:
            url = "https://home.juice.net/Portal/SetLimit"
            body = {
                "__RequestVerificationToken": self.loToken,
                "unitID": juiceBox.deviceId,
                "allowedC": maxCurrent,
            }
            resp = self.session.request("POST", url, headers=JbInterface.XHR_HEADERS, data=body)

            if resp.status_code != 200:
                raise HTTPException.fromError(resp)

            logging.info(f"{juiceBox.name} maximum current changed"
                         f" from {juiceBox.maxCurrent} to {maxCurrent} A")
            juiceBox.maxCurrent = maxCurrent
        else:
            logging.info(f"{juiceBox.name} maximum current already set to {maxCurrent} A")
    # end setMaxCurrent(JbDetails, int)

    def setNewMaximums(self, juiceBoxA: JbDetails, maxAmpsA: int,
                       juiceBoxB: JbDetails) -> None:
        """Set JuiceBox maximum currents, decrease one before increasing the other

        :param juiceBoxA: One of the JuiceBoxes to set
        :param maxAmpsA: The desired maximum current for juiceBoxA
        :param juiceBoxB: The other JuiceBox to set (gets remaining current)
        """
        maxAmpsA = self.limitCurrent(juiceBoxA, maxAmpsA)
        maxAmpsB = self.limitCurrent(juiceBoxB, self.totalCurrent - maxAmpsA)
        maxAmpsA = self.totalCurrent - maxAmpsB

        if maxAmpsA < juiceBoxA.maxCurrent:
            # decreasing juiceBoxA limit, so do it first
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
            self.setMaxCurrent(juiceBoxB, maxAmpsB)
        else:
            self.setMaxCurrent(juiceBoxB, maxAmpsB)
            self.setMaxCurrent(juiceBoxA, maxAmpsA)
    # end setNewMaximums(JbDetails, int, JbDetails)

    def limitCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> int:
        """Return a maximum current that does not exceed the wire rating of
           the JuiceBox and complies with J1772's minimum plug-in current"""
        maxCurrent = juiceBox.limitToWireRating(maxCurrent)

        # Use a minimum charge current limit when plugged-in - Teslas seem to need this
        if juiceBox.pluggedIn() and maxCurrent < self.minPluggedCurrent:
            maxCurrent = self.minPluggedCurrent

        return maxCurrent
    # end limitCurrent(JbDetails, int)

    def close(self) -> None:
        try:
            if self.loToken:
                self.logOut()
        finally:
            self.session.close()
    # end close()

# end class JbInterface
