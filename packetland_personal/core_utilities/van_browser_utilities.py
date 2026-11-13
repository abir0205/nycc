"""
Shared browser session helpers for driving VoteBuilder.

Some VAN work has no API behind it (printing turf packets), so those flows drive Chrome instead. Login, the state picker, the driver setup and
the element helpers live here so every flow that needs a VoteBuilder session
uses the same one.

Ported from an earlier internal VAN automation.
"""

import os
import string
import subprocess
import time

import pyotp
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from core_utilities.gcp_utilities import get_secret

VOTEBUILDER_HOME = "https://www.votebuilder.com/Default.aspx"
VOTEBUILDER_LOGIN = "https://www.votebuilder.com/Login.aspx?OIDF=1"
# Both the state picker and the committee switcher live on this page.
SELECT_COMMITTEE_URL = "https://www.votebuilder.com/SelectCommittee.aspx"

STATE_SELECT_CSS = "select[id*='SelectMultiStateID']"
COMMITTEE_SELECT_CSS = "select[id$='DropDownListCommittees']"
CONTINUE_BUTTON_CSS = "input[id*='ButtonContinue']"

# The two VAN databases, named as their header tabs label them.
MY_CAMPAIGN = "My Campaign"
MY_VOTERS = "My Voters"
DATABASE_TAB_CSS = ".app-nav .nav-tabs > li"

# ActionID login for the browser flows: email, password and TOTP seed.
ACTIONID_SECRET = (
    "[PATH]"
)
# The account a flow logs in as unless it names another one in the secret.
DEFAULT_ACTIONID_USER = "[EMAIL]"

TIMEOUT_SECONDS = 30


def wait(seconds=2):
    time.sleep(seconds)


def van_obfuscate(van_id: int) -> str:
    """
    Encodes a VAN id the way its URLs do: saved list 123456 is EID042E1C.

    Args:
        van_id (int): The raw VAN identifier.
    Returns:
        str: The obfuscated form, without the leading EID.
    """
    alphabet = string.ascii_uppercase
    return (hex(int(van_id))[2:][::-1] + alphabet[int(van_id) % 17]).upper()


def create_driver(headless=True):
    """
    Chrome setup lifted from an earlier internal VAN automation.

    CHROME_BIN and CHROMEDRIVER_PATH point at the apt-installed chromium in the
    container image. Unset, Selenium Manager finds the local Chrome as before.
    """
    chrome_options = webdriver.ChromeOptions()

    chrome_binary = os.environ.get("CHROME_BIN")
    if chrome_binary:
        chrome_options.binary_location = chrome_binary

    driver_path = os.environ.get("CHROMEDRIVER_PATH")
    if driver_path:
        service = webdriver.ChromeService(executable_path=driver_path, log_output=subprocess.STDOUT)
    else:
        service = webdriver.ChromeService(log_output=subprocess.STDOUT)

    if headless:
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1600x1200")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")

    return webdriver.Chrome(options=chrome_options, service=service)


def get_verification_code(totp_secret):
    return pyotp.TOTP(totp_secret).now()


def resolve_actionid_credentials(
    user: str = DEFAULT_ACTIONID_USER, secret_name: str = ACTIONID_SECRET
) -> tuple:
    """
    Pulls the email, password and TOTP seed for one login account.

    The secret holds each account as {email: {"password", "totp_secret"}}, and
    user picks which one. Emails are matched ignoring case.
    """
    secret_blob = get_secret(secret_name)
    accounts = {email.lower(): email for email in secret_blob}
    email = accounts.get(user.lower())
    if email is None:
        raise ValueError(
            f"No ActionID entry for {user} in {secret_name}, found {sorted(secret_blob)}"
        )

    creds = secret_blob[email]
    password = creds.get("password")
    totp_secret = creds.get("totp_secret")
    if not password or not totp_secret:
        raise ValueError(f"ActionID entry for {email} needs a password and a totp_secret")

    # VAN shows the seed in space separated groups, but pyotp wants it unbroken.
    return email, password, totp_secret.replace(" ", "")


### ---------------------------------------------------------------------- ###
## ELEMENT HELPERS
### ---------------------------------------------------------------------- ###

def find_element(driver, by_object, descriptor, delay=True, wait_sec=TIMEOUT_SECONDS):
    presence = EC.presence_of_element_located((by_object, descriptor))
    WebDriverWait(driver, wait_sec).until(presence)
    if delay:
        wait()
    return driver.find_element(by_object, descriptor)


def find_and_click(driver, by_object, descriptor, delay=True):
    """Clicks through JS, which is what survives VAN's overlays and sticky headers."""
    element = find_element(driver, by_object, descriptor, delay=delay)
    driver.execute_script("arguments[0].click();", element)


def find_and_send_keys(driver, by_object, descriptor, keys, delay=True):
    element = find_element(driver, by_object, descriptor, delay=delay)
    element.send_keys(keys)


def find_and_select(driver, by_object, descriptor, text, delay=True):
    element = find_element(driver, by_object, descriptor, delay=delay)
    Select(element).select_by_visible_text(text)


### ---------------------------------------------------------------------- ###
## SESSION
### ---------------------------------------------------------------------- ###

def actionid_login(driver, username: str, password: str, totp_secret: str):
    """
    Walks the ActionID login, including the TOTP second factor.

    The 2FA code is generated rather than typed by a human, so flows using this
    can run headless and unattended.
    """
    driver.delete_all_cookies()
    driver.get(VOTEBUILDER_LOGIN)

    find_and_click(driver, By.CSS_SELECTOR, "a[id*='OpenIDSelectorLinkLink']", delay=True)

    find_and_send_keys(driver, By.ID, "1-email", username)
    password_input = find_element(driver, By.CSS_SELECTOR, "input[name='password']")
    password_input.send_keys(password)
    wait()
    password_input.submit()

    code_input = find_element(driver, By.CSS_SELECTOR, "input[id='code'][name='code']")
    code_input.send_keys(get_verification_code(totp_secret))
    code_input.submit()
    wait(4)


def manual_login(driver):
    """Fallback for when there are no stored ActionID credentials."""
    driver.get(VOTEBUILDER_LOGIN)
    print("\nLog into VoteBuilder in the browser window that just opened.")
    input("Press Enter here once you are through the login: ")


def database_tabs(driver) -> dict:
    """
    Maps each database VAN offers to its header tab, as {name: {href, active}}.

    The tabs render twice, once for wide screens and once as a dropdown for
    narrow ones, so only the first tab under each name is kept.
    """
    return driver.execute_script(
        """
        const tabs = {};
        for (const li of document.querySelectorAll(arguments[0])) {
            const link = li.querySelector('a');
            if (!link) { continue; }
            const name = link.textContent.trim();
            if (!name || tabs[name]) { continue; }
            tabs[name] = {href: link.href, active: li.classList.contains('active')};
        }
        return tabs;
        """,
        DATABASE_TAB_CSS,
    )


def current_database(driver) -> str | None:
    """Returns the database the session is in, off the active header tab."""
    for name, tab in database_tabs(driver).items():
        if tab["active"]:
            return name
    return None


def choose_database(driver, database: str) -> bool:
    """
    Puts the session in My Voters or My Campaign, and says whether it moved.

    VAN defaults to whichever database the account last used, so a flow that
    never picks one is not in a known place. The switch is the header tab: the
    database in use is the active one and the other carries a link back to
    SelectCommittee.aspx with its DatabaseTabID. Following that link drops any
    state already picked, which is why this runs before the state.
    """
    tabs = database_tabs(driver)
    if not tabs:
        raise ValueError(
            f"No database tabs in the VAN header at {driver.current_url}, so there "
            f"is no way to tell whether the session is in {database}"
        )
    if database not in tabs:
        raise ValueError(
            f"The account has no {database} tab. VAN offers: {sorted(tabs)}"
        )
    if tabs[database]["active"]:
        return False

    driver.get(tabs[database]["href"])
    return True


def choose_state(driver, state_name: str) -> bool:
    """
    Picks a state on SelectCommittee.aspx.

    Always selects by visible text. The option values are not derivable from the
    state: some are bare state abbreviations (Arizona is "AZ") and some are DNC prefixed
    (Hawaii is "DNCHI"), so matching on anything but the label breaks.

    The page does not always appear, so a timeout means it was not needed.
    """
    try:
        find_and_select(driver, By.CSS_SELECTOR, STATE_SELECT_CSS, text=state_name)
        return True
    except TimeoutException:
        return False


def state_labels(driver) -> dict:
    """
    Maps state abbreviation to the label the state picker shows for it.

    The option values are not consistent, so a state abbreviation is matched against
    both shapes VAN uses: bare ("AZ") and DNC prefixed ("DNCHI"). Selection
    still goes by visible text, which is the only handle that works.
    """
    options = driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (!sel) { return []; }
        return Array.from(sel.options)
            .map(o => ({value: o.value.trim(), text: o.text.trim()}))
            .filter(o => o.value && o.text);
        """,
        STATE_SELECT_CSS,
    )

    labels = {}
    for option in options:
        value = option["value"].upper()
        state_abbrev = value[3:] if value.startswith("DNC") else value
        if len(state_abbrev) == 2:
            labels[state_abbrev] = option["text"]
    return labels


def select_committee(driver, committee_name: str) -> str | None:
    """
    Picks a committee on SelectCommittee.aspx, if the account has any.

    The native select is wrapped by select2, which hides it behind its own UI, so
    setting the value on the DOM and firing change is what registers. Its inline
    onchange is what copies the id into the HiddenCommitteeID field the form
    actually posts, and jQuery's trigger runs that inline handler, so the id is
    read back afterwards to confirm it took.

    Returns the committee id, or None when the account has no committee dropdown.
    """
    matched = driver.execute_script(
        """
        const sel = document.querySelector(arguments[0]);
        if (!sel) { return {missing: true}; }
        const wanted = arguments[1].trim();
        const opt = Array.from(sel.options).find(o => o.text.trim() === wanted);
        if (!opt) {
            return {options: Array.from(sel.options).map(o => o.text.trim()).filter(t => t)};
        }
        sel.value = opt.value;
        if (window.$j) { $j(sel).trigger('change'); }
        else { sel.dispatchEvent(new Event('change', {bubbles: true})); }
        const hidden = document.getElementsByName('HiddenCommitteeID')[0];
        return {value: opt.value, hidden: hidden ? hidden.value : null};
        """,
        COMMITTEE_SELECT_CSS,
        committee_name,
    )

    if matched.get("missing"):
        return None
    if "value" not in matched:
        raise ValueError(
            f"Committee '{committee_name}' is not in the switcher. "
            f"Available: {matched.get('options')}"
        )

    # The hidden field is what posts, so a mismatch means the change never fired.
    hidden = matched.get("hidden")
    if hidden is not None and hidden != matched["value"]:
        raise ValueError(
            f"Selecting '{committee_name}' did not take: the form still posts committee "
            f"{hidden!r} rather than {matched['value']!r}"
        )
    return matched["value"]


def click_continue(driver):
    """Clicks Next on SelectCommittee.aspx."""
    find_and_click(driver, By.CSS_SELECTOR, CONTINUE_BUTTON_CSS, delay=True)


def settle(driver, timeout: int = TIMEOUT_SECONDS):
    """
    Waits for the page to finish rendering.

    Full page loads move readyState, but VAN does a lot of its work in UpdatePanel
    partial postbacks that never touch it, so this also waits on the ASP.NET AJAX
    request manager. The state dropdown is one of those: picking a state fires
    __doPostBack and the committee list only appears once it comes back.
    """
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script(
            "return !(window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager)"
            " || !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack();"
        )
    )
    wait(1)
