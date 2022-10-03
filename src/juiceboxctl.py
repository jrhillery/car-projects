
import json
import logging
from argparse import ArgumentParser, Namespace
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Type
from urllib.parse import urljoin

import sys
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.expected_conditions import (
    element_to_be_clickable, invisibility_of_element,
    visibility_of, visibility_of_element_located)
from selenium.webdriver.support.wait import WebDriverWait
from time import sleep

from util.configure import Configure


class JuiceBoxException(Exception):
    """Class for handled exceptions"""

    @classmethod
    def fromXcp(cls, unableMsg: str, xcption: WebDriverException):
        """Factory method for WebDriverExceptions"""
        return cls(f"Unable to {unableMsg}, {xcption.__class__.__name__}: {xcption.msg}")
    # end fromXcp(str, WebDriverException)

# end class JuiceBoxException


class JuiceBoxDetails(object):
    """Details of a JuiceBox"""

    deviceId: str
    detailUrl: str
    name: str
    status: str
    maxCurrent: int

    def __init__(self, deviceId: str, baseUrl: str):
        self.deviceId = deviceId
        self.detailUrl = urljoin(baseUrl, f"/Portal/Details?unitID={deviceId}")
    # end __init__(str, str)

    def statusStr(self) -> str:
        return (f"{self.name} is {self.status}"
                f" with maximum current {self.maxCurrent} A")
    # end statusStr()

    def __str__(self) -> str:
        retStr = f"id[{self.deviceId}]"

        if hasattr(self, "name"):
            retStr += f" name[{self.name}]"

        return retStr
    # end __str__()

# end class JuiceBoxDetails


class JuiceBoxCtl(AbstractContextManager["JuiceBoxCtl"]):
    """Controls JuiceBox devices"""
    LOG_IN = "https://home.juice.net/Account/Login"
    LOG_IN_FORM_LOCATOR = By.CSS_SELECTOR, "form.form-vertical"
    MAX_CURRENT_LOCATOR = By.CSS_SELECTOR, "input#Status_allowed_C"

    def __init__(self, args: Namespace | None = None):
        self.specifiedJuiceBoxName: str | None = None if args is None else args.juiceBoxName
        self.specifiedMaxAmps: int | None = None if args is None else args.maxAmps
        self.webDriver: WebDriver | None = None
        self.localWait: WebDriverWait | None = None
        self.remoteWait: WebDriverWait | None = None
        self.loggedIn = False

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

    def openBrowser(self) -> WebDriver:
        """Get web driver and open browser"""
        try:
            self.webDriver = webdriver.Chrome()
            self.localWait = WebDriverWait(self.webDriver, 5)
            self.remoteWait = WebDriverWait(self.webDriver, 15)

            return self.webDriver
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp("open browser", e) from e
    # end openBrowser()

    def logIn(self) -> None:
        """Log-in to JuiceNet"""
        doingMsg = "open log-in page " + JuiceBoxCtl.LOG_IN
        try:
            self.webDriver.get(JuiceBoxCtl.LOG_IN)

            doingMsg = "find log-in form"
            liForm = self.webDriver.find_element(*JuiceBoxCtl.LOG_IN_FORM_LOCATOR)

            doingMsg = "enter email"
            liForm.find_element(By.CSS_SELECTOR, "input#Email").send_keys(
                self.loginCreds["email"])

            doingMsg = "enter password"
            liForm.find_element(By.CSS_SELECTOR, "input#Password").send_keys(
                self.loginCreds["password"])

            doingMsg = "submit log-in form"
            liForm.submit()
            self.remoteWait.until(
                element_to_be_clickable((By.CSS_SELECTOR, "a#update-unit-list-button")),
                "Timed out waiting to log-in")
            self.loggedIn = True
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp(doingMsg, e) from e
    # end logIn()

    def logOut(self) -> None:
        """Log-out from JuiceNet"""
        try:
            self.webDriver.find_element(By.CSS_SELECTOR, "form#logoutForm").submit()
            self.remoteWait.until(
                visibility_of_element_located(JuiceBoxCtl.LOG_IN_FORM_LOCATOR),
                "Timed out waiting to log out")

            self.loggedIn = False
            # give us a change to see we are logged out
            sleep(0.75)
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp("log out", e) from e
    # end logOut()

    def storeDetails(self, juiceBox: JuiceBoxDetails) -> None:
        """Update details of the JuiceBoxDetails argument"""
        doingMsg = "request details via " + juiceBox.detailUrl
        try:
            self.webDriver.get(juiceBox.detailUrl)

            doingMsg = "store name"
            juiceBox.name = self.webDriver.find_element(
                By.CSS_SELECTOR, "h3.panel-title").text

            doingMsg = "store status"
            juiceBox.status = self.webDriver.find_element(
                By.CSS_SELECTOR, "span#statusText").text

            doingMsg = "store current limit"
            juiceBox.maxCurrent = int(self.webDriver.find_element(
                *JuiceBoxCtl.MAX_CURRENT_LOCATOR).get_dom_attribute("value"))
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp(doingMsg, e) from e
    # end storeDetails(JuiceBoxDetails)

    def getStateOfJuiceBoxes(self) -> list[JuiceBoxDetails]:
        """Get all active JuiceBoxes and their latest states."""
        doingMsg = "find JuiceBoxes"
        try:
            panels = self.webDriver.find_elements(By.CSS_SELECTOR, "div.unit-info-container")

            doingMsg = "get base URL"
            baseUrl = self.webDriver.current_url
            juiceBoxes = []

            doingMsg = "extract device ids"
            for panel in panels:
                deviceId = panel.get_dom_attribute("data-unitid")
                juiceBoxes.append(JuiceBoxDetails(deviceId, baseUrl))
            # end for

            for juiceBox in juiceBoxes:
                self.storeDetails(juiceBox)
            # end for

            return juiceBoxes
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp(doingMsg, e) from e
    # end getStateOfJuiceBoxes()

    def setMaxCurrent(self, juiceBox: JuiceBoxDetails, maxCurrent: int) -> None:
        # JuiceBox won't accept max of 0, so use 1 instead
        if maxCurrent < 1:
            maxCurrent = 1

        if maxCurrent != juiceBox.maxCurrent:
            doingMsg = "navigate to details via " + juiceBox.detailUrl
            try:
                self.webDriver.get(juiceBox.detailUrl)

                doingMsg = "send maximum current characters"
                inputFld = self.webDriver.find_element(*JuiceBoxCtl.MAX_CURRENT_LOCATOR)
                inputFld.clear()
                inputFld.send_keys(str(maxCurrent))

                doingMsg = "store spinner element"
                spinner = self.webDriver.find_element(
                    By.CSS_SELECTOR, "button#buttonAllowedUpdate > i")

                doingMsg = "update maximum current"
                spinner.get_property("parentElement").click()
                try:
                    self.localWait.until(visibility_of(spinner))
                except TimeoutException:
                    # sometimes we miss the spinner's appearance
                    pass
                self.remoteWait.until(invisibility_of_element(spinner),
                                      "Timed out waiting to update maximum current")

                logging.info(f"{juiceBox.name} maximum current changed"
                             f" from {juiceBox.maxCurrent} to {maxCurrent} A")
                juiceBox.maxCurrent = maxCurrent
            except WebDriverException as e:
                raise JuiceBoxException.fromXcp(doingMsg, e) from e
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

        if self.loggedIn:
            self.logOut()

        return None
    # end __exit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        specifiedJuiceBox: JuiceBoxDetails | None = None
        otherJuiceBox: JuiceBoxDetails | None = None

        with self.openBrowser(), self:
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
