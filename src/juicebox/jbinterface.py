
import json
import logging
from time import perf_counter

from pyquery import PyQuery
from requests import Response, Session

from util import Configure, HTTPException
from . import JbDetails


class JbInterface(object):
    """Provide an interface to authorized JuiceBox devices"""
    NOT_CACHED_HEADER = {"Cache-Control": "max-age=0"}
    XHR_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, minPluggedCurrent: int, totalCurrent: int, session: Session):
        self.minPluggedCurrent: int = minPluggedCurrent
        self.totalCurrent: int = totalCurrent
        self.session = session
        self.loToken: str | None = None
    # end __init__(int, int, Session)

    @classmethod
    def create(cls, minPluggedCurrent: int, totalCurrent: int):
        """Factory method

        :param minPluggedCurrent: The minimum current limit to set when a car is plugged in
        :param totalCurrent: The total current avaible to all Juiceboxes
        """
        session = Session()

        # provide another default request header
        session.headers.update({"Accept-Language": "en-US,en;q=0.9"})

        return cls(minPluggedCurrent, totalCurrent, session)
    # end create(int, int)

    @staticmethod
    def logInBody(resp: Response) -> dict[str, str]:
        """Return a log-in post request body

        :param resp: The response from an initial account login get request
        :return: A dictionary representing the body of a log-in post request
        """
        try:
            liToken: str = PyQuery(resp.text).find(
                "form.form-vertical > input[name='__RequestVerificationToken']").val()
        except Exception as e:
            raise HTTPException.fromXcp(e, resp, "account login") from e

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

    async def logIn(self) -> None:
        """Log-in to JuiceNet"""
        url = "https://home.juice.net/Account/Login"

        with self.session.request("GET", url) as resp:
            if resp.status_code != 200:
                raise HTTPException.fromError(resp, "login get")
            body = self.logInBody(resp)

        with self.session.request("POST", url, headers=self.NOT_CACHED_HEADER,
                                  data=body) as resp:
            if resp.status_code != 200:
                raise HTTPException.fromError(resp, "login post")

            try:
                self.loToken = PyQuery(resp.text).find(
                    "form#logoutForm > input[name='__RequestVerificationToken']").val()
            except Exception as e:
                raise HTTPException.fromXcp(e, resp, "verification token") from e
    # end logIn()

    async def logOut(self) -> None:
        """Log-out from JuiceNet"""
        url = "https://home.juice.net/Account/LogOff"
        body = {"__RequestVerificationToken": self.loToken}

        with self.session.request("POST", url, headers=self.NOT_CACHED_HEADER,
                                  data=body) as resp:
            self.loToken = None

            if resp.status_code != 200:
                raise HTTPException.fromError(resp, "account logoff")
    # end logOut()

    async def getStateOfJuiceBoxes(self) -> list[JbDetails]:
        """Get all active JuiceBoxes and their latest states

        :return: A list with details of each JuiceBox in the account
        """
        url = "https://home.juice.net/Portal/GetUserUnitsJson"
        body = {"__RequestVerificationToken": self.loToken}

        with self.session.request("POST", url, headers=self.XHR_HEADERS, data=body) as resp:
            if resp.status_code == 200:
                try:
                    unitMap: dict[str, dict] = resp.json()["Units"]
                    juiceBoxStates = unitMap.values()
                except Exception as e:
                    raise HTTPException.fromXcp(e, resp, "all active JuiceBoxes") from e

                juiceBoxes = [JbDetails(jbState) for jbState in juiceBoxStates]
                start = perf_counter()

                for jb in juiceBoxes:
                    await self.addMoreDetails(jb)
                print(f"add more details: {perf_counter() - start:.7f}s")

                return juiceBoxes
            else:
                raise HTTPException.fromError(resp, "all active JuiceBoxes")
    # end getStateOfJuiceBoxes()

    async def addMoreDetails(self, juiceBox: JbDetails) -> JbDetails:
        """Augment details of the specified JuiceBox

        :param juiceBox: Details of the JuiceBox to augment
        :return: The updated JuiceBox details
        """
        url = "https://home.juice.net/Portal/Details"
        qryParms = {"unitID": juiceBox.deviceId}

        with self.session.request("GET", url, params=qryParms) as resp:
            if resp.status_code == 200:
                try:
                    pQry = PyQuery(resp.text)
                    juiceBox.wireRating = int(pQry.find("input#wire_rating").val())
                except Exception as e:
                    raise HTTPException.fromXcp(e, resp, juiceBox.name) from e
            else:
                raise HTTPException.fromError(resp, juiceBox.name)

        return juiceBox
    # end addMoreDetails(JbDetails)

    async def setMaxCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> None:
        """Set the JuiceBox maximum current as close as possible to a specified maximum

        :param juiceBox: Details of the JuiceBox to set
        :param maxCurrent: Requested new maximum current limit
        """
        if maxCurrent < 1:
            # JuiceBox doesn't accept max of 0, so use 1 instead
            maxCurrent = 1
        maxCurrent = juiceBox.limitToWireRating(maxCurrent)

        if maxCurrent != juiceBox.maxCurrent:
            url = "https://home.juice.net/Portal/SetLimit"
            body = {
                "__RequestVerificationToken": self.loToken,
                "unitID": juiceBox.deviceId,
                "allowedC": maxCurrent,
            }
            with self.session.request("POST", url, headers=self.XHR_HEADERS, data=body) as resp:
                if resp.status_code != 200:
                    raise HTTPException.fromError(resp, juiceBox.name)

            logging.info(f"{juiceBox.name} maximum current changed"
                         f" from {juiceBox.maxCurrent} to {maxCurrent} A")
            juiceBox.maxCurrent = maxCurrent
        else:
            logging.info(f"{juiceBox.name} maximum current already set to {maxCurrent} A")
    # end setMaxCurrent(JbDetails, int)

    async def setNewMaximums(self, juiceBoxA: JbDetails, maxAmpsA: int,
                             juiceBoxB: JbDetails) -> None:
        """Set JuiceBox maximum currents, decrease one before increasing the other

        :param juiceBoxA: Details of one of the JuiceBoxes to set
        :param maxAmpsA: The desired maximum current for juiceBoxA
        :param juiceBoxB: Details of the other JuiceBox to set (gets remaining current)
        """
        maxAmpsA = self.limitCurrent(juiceBoxA, maxAmpsA)
        maxAmpsB = self.limitCurrent(juiceBoxB, self.totalCurrent - maxAmpsA)
        maxAmpsA = self.totalCurrent - maxAmpsB

        if maxAmpsA < juiceBoxA.maxCurrent:
            # decreasing juiceBoxA limit, so do it first
            await self.setMaxCurrent(juiceBoxA, maxAmpsA)
            await self.setMaxCurrent(juiceBoxB, maxAmpsB)
        else:
            await self.setMaxCurrent(juiceBoxB, maxAmpsB)
            await self.setMaxCurrent(juiceBoxA, maxAmpsA)
    # end setNewMaximums(JbDetails, int, JbDetails)

    def limitCurrent(self, juiceBox: JbDetails, maxCurrent: int) -> int:
        """Return a maximum current that does not exceed the wire rating of
           the JuiceBox and complies with J1772's minimum plug-in current

        :param juiceBox: Details of the relevant JuiceBox
        :param maxCurrent: The desired maximum current
        :return: The maximum current that satisfies restrictions
        """
        maxCurrent = juiceBox.limitToWireRating(maxCurrent)

        # Use a minimum charge current limit when plugged in - Teslas seem to need this
        if juiceBox.pluggedIn() and maxCurrent < self.minPluggedCurrent:
            maxCurrent = self.minPluggedCurrent

        return maxCurrent
    # end limitCurrent(JbDetails, int)

    async def aclose(self) -> None:
        """Close this instance and free up resources"""
        try:
            if self.loToken:
                await self.logOut()
        finally:
            self.session.close()
    # end aclose()

# end class JbInterface
