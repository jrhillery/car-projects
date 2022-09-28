
import json
import logging
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Type

import sys
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.expected_conditions import (
    element_to_be_clickable, visibility_of_element_located)
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


class JuiceBoxCtl(AbstractContextManager["JuiceBoxCtl"]):
    """Controls JuiceBox devices"""
    LOG_IN = "https://home.juice.net/Account/Login"
    LOG_IN_FORM_LOCATOR = By.CSS_SELECTOR, "form.form-vertical"
    EMAIL_LOCATOR = By.CSS_SELECTOR, "input#Email"
    PASSWORD_LOCATOR = By.CSS_SELECTOR, "input#Password"
    UPDATE_DEVICE_LIST_LOCATOR = By.CSS_SELECTOR, "a#update-unit-list-button"
    LOG_OUT_FORM_LOCATOR = By.CSS_SELECTOR, "form#logoutForm"

    def __init__(self):
        self.webDriver: WebDriver | None = None
        self.localWait: WebDriverWait | None = None
        self.remoteWait: WebDriverWait | None = None
        self.loggedIn = False

        with open(Path(Configure.findParmPath(), "juicenetlogincreds.json"),
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

    def logIn(self):
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
            raise JuiceBoxException.fromXcp(doingMsg, e)
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
            raise JuiceBoxException.fromXcp("logging out", e) from e
    # end logOut()

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
            sleep(9)
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
