
import json
import logging
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
    EMAIL_LOCATOR = By.CSS_SELECTOR, "input#Email"
    PASSWORD_LOCATOR = By.CSS_SELECTOR, "input#Password"
    UPDATE_DEVICE_LIST_LOCATOR = By.CSS_SELECTOR, "a#update-unit-list-button"
    LOG_OUT_FORM_LOCATOR = By.CSS_SELECTOR, "form#logoutForm"
    UNIT_PANEL_LOCATOR = By.CSS_SELECTOR, "div.unit-info-container"
    UNIT_NAME_LOCATOR = By.CSS_SELECTOR, "h3.panel-title"
    UNIT_STATUS_LOCATOR = By.CSS_SELECTOR, "span#statusText"
    MAX_CURRENT_LOCATOR = By.CSS_SELECTOR, "input#Status_allowed_C"

    def __init__(self):
        self.webDriver: WebDriver | None = None
        self.localWait: WebDriverWait | None = None
        self.remoteWait: WebDriverWait | None = None
        self.loggedIn = False
        self.dianes: JuiceBoxDetails | None = None
        self.johns: JuiceBoxDetails | None = None

        with open(Configure.findParmPath().joinpath("juicenetlogincreds.json"),
                  "r", encoding="utf-8") as credFile:
            self.loginCreds = json.load(credFile)
    # end __init__()

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
            liForm.find_element(*JuiceBoxCtl.EMAIL_LOCATOR).send_keys(
                self.loginCreds["email"])

            doingMsg = "enter password"
            liForm.find_element(*JuiceBoxCtl.PASSWORD_LOCATOR).send_keys(
                self.loginCreds["password"])

            doingMsg = "submit log-in form"
            liForm.submit()
            self.remoteWait.until(
                element_to_be_clickable(JuiceBoxCtl.UPDATE_DEVICE_LIST_LOCATOR),
                "Timed out waiting to log-in")
            self.loggedIn = True
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp(doingMsg, e) from e
    # end logIn()

    def logOut(self) -> None:
        """Log-out from JuiceNet"""
        try:
            self.webDriver.find_element(*JuiceBoxCtl.LOG_OUT_FORM_LOCATOR).submit()
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
                *JuiceBoxCtl.UNIT_NAME_LOCATOR).text

            doingMsg = "store status"
            juiceBox.status = self.webDriver.find_element(
                *JuiceBoxCtl.UNIT_STATUS_LOCATOR).text

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
            panels = self.webDriver.find_elements(*JuiceBoxCtl.UNIT_PANEL_LOCATOR)

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
        doingMsg = "navigate to details via " + juiceBox.detailUrl
        try:
            self.webDriver.get(juiceBox.detailUrl)

            doingMsg = "send maximum current characters"
            inputFld = self.webDriver.find_element(*JuiceBoxCtl.MAX_CURRENT_LOCATOR)
            inputFld.clear()
            inputFld.send_keys(str(maxCurrent))

            doingMsg = "store spinner element"
            spinner = inputFld.get_property("parentElement").find_element(
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
            oldMax = juiceBox.maxCurrent
            juiceBox.maxCurrent = maxCurrent

            logging.info(f"{juiceBox.name} maximum current changed"
                         f" from {oldMax} A to {maxCurrent} A")
        except WebDriverException as e:
            raise JuiceBoxException.fromXcp(doingMsg, e) from e
    # end setMaxCurrent(JuiceBoxDetails, int)

    def __exit__(self, exc_type: Type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool | None:

        if self.loggedIn:
            self.logOut()

        return None
    # end __exit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        with self.openBrowser(), self:
            self.logIn()
            juiceBoxes = self.getStateOfJuiceBoxes()

            for juiceBox in juiceBoxes:
                if juiceBox.name.startswith("Diane's"):
                    self.dianes = juiceBox
                elif juiceBox.name.startswith("John's"):
                    self.johns = juiceBox
            # end for

            if not self.dianes or not self.johns:
                raise JuiceBoxException(f"Unable to locate both JuiceBoxes,"
                                        f" found {[jb.name for jb in juiceBoxes]}")

            self.setMaxCurrent(self.dianes, 25)
            sleep(8)
        # end with
    # end main()

# end class JuiceBoxCtl


if __name__ == "__main__":
    Configure.logToFile()
    try:
        juiceCtl = JuiceBoxCtl()
        juiceCtl.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug(f"{xcpt.__class__.__name__} suppressed:", exc_info=xcpt)
