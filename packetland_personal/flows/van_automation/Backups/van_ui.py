"""
Selenium page objects for the VoteBuilder screens the packet flows drive: the
committee switcher, folder detail, the print region wizard, and PDF print jobs.

The locators they run against live in constants.py, and the low-level reading
and setting of a control lives in controls.py. What is left here is the pages
themselves: what a flow asks for, in the order VAN needs it asked.
"""

import json
import os
import re
import time

from urllib.parse import quote, urljoin

import requests

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.expected_conditions import staleness_of
from selenium.webdriver.support.ui import WebDriverWait

from core_utilities.van_browser_utilities import (
    CONTINUE_BUTTON_CSS,
    MY_CAMPAIGN,
    SELECT_COMMITTEE_URL,
    STATE_SELECT_CSS,
    TIMEOUT_SECONDS,
    actionid_login,
    choose_database,
    choose_state,
    click_continue,
    current_database,
    find_and_click,
    find_element,
    manual_login,
    select_committee,
    state_labels,
    van_obfuscate,
)

from flows.van_automation import controls
# Star import so every locator stays reachable as van_ui.NAME, which is how the
# flows and tests already ask for them. constants.py imports nothing itself, so
# this can only bring in names.
from flows.van_automation.constants import *
from flows.van_automation.errors import VanUiError

# Starts the scripts run on the PDF Print Jobs list. Since Oct 14 2026 that page walks
# the call stack (fn.caller, up and up) when a script reads it, and the walk throws
# "'caller', 'callee', and 'arguments' properties may not be accessed on strict mode
# functions" once it reaches ChromeDriver's own wrapper. Chrome hides a strict-mode
# caller, so a strict script ends the walk at itself. Add it to any script that starts
# failing the same way on another page.
USE_STRICT = "'use strict';\n"


class VanBrowser:
    """Wraps a logged-in VoteBuilder session."""

    def __init__(self, driver, timeout: int = TIMEOUT_SECONDS):
        self.driver = driver
        self.timeout = timeout
        self._state_labels = None
        # The state the session is on, for the screens VAN renders differently by
        # state. None until a session switch has picked one.
        self.state = None

    def _wait(self, timeout: int | None = None) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout or self.timeout)

    def _settle(self):
        """
        core_utilities.settle, with both checks run strict (see USE_STRICT): the page has
        loaded, and no UpdatePanel postback is still in flight.
        """
        self._wait().until(
            lambda d: d.execute_script(USE_STRICT + "return document.readyState;") == "complete"
        )
        self._wait().until(
            lambda d: d.execute_script(
                USE_STRICT
                + "return !(window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager)"
                " || !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack();"
            )
        )
        time.sleep(1)

    ### ------------------------------------------------------------------ ###
    ## LOGIN AND SESSION
    ### ------------------------------------------------------------------ ###

    def login(self, username: str, password: str, totp_secret: str):
        actionid_login(self.driver, username, password, totp_secret)

    def wait_for_manual_login(self):
        manual_login(self.driver)
        self._settle()

    def _open_switcher(self):
        self.driver.get(SELECT_COMMITTEE_URL)
        self._settle()

    def _choose_state(self, state: str):
        """
        Selects a state by its abbreviation.

        The picker only matches on visible text, and its option values are not
        derivable from the abbreviation (Arizona is AZ, Hawaii is DNCHI), so the
        picker itself is read once and cached to get from one to the other.

        An account with access to one state gets no picker at all, and its session is
        already in that state, so there is nothing to pick.
        """
        if not self.driver.find_elements(By.CSS_SELECTOR, STATE_SELECT_CSS):
            self.state = state
            return

        if self._state_labels is None:
            self._state_labels = state_labels(self.driver)
        if state not in self._state_labels:
            raise VanUiError(f"No state in the VAN picker matches {state}")

        choose_state(self.driver, self._state_labels[state])
        self.state = state
        # Picking a state fires a partial postback that rebuilds the committee
        # list, so nothing after this can run until it lands.
        self._settle()

    def _choose_database(self, database: str):
        """
        Puts the session in the database the flow works in.

        VAN opens in whichever database the shared login last used, so this is
        set every time rather than trusted. Switching reloads the switcher, so
        the state is picked after it, not before.
        """
        if choose_database(self.driver, database):
            self._settle()

    def _check_database(self, database: str):
        """
        Fails if the session did not land in the database the flow asked for.

        A run in the wrong database does not error on its own: VAN just serves
        the other database's pages, so this is checked rather than assumed.
        """
        landed = current_database(self.driver)
        if landed != database:
            raise VanUiError(
                f"The session is in {landed or 'no database VAN names'} after the "
                f"switcher, not {database}"
            )

    def switch_session(self, state: str, committee_name: str | None = None,
                       database: str = MY_CAMPAIGN):
        """Puts the session on the right database, state and committee."""
        self._open_switcher()
        self._choose_database(database)
        self._choose_state(state)

        if committee_name is not None:
            chosen = select_committee(self.driver, committee_name)
            if chosen is None:
                raise VanUiError(
                    f"{state} has no committee dropdown, but {committee_name} was expected"
                )
            self._settle()

        # An account with nothing to pick may never be shown the switcher's Next.
        # The database check below still has to pass either way.
        if self.driver.find_elements(By.CSS_SELECTOR, CONTINUE_BUTTON_CSS):
            click_continue(self.driver)
            self._settle()
        self._check_database(database)

    ### ------------------------------------------------------------------ ###
    ## LOW LEVEL HELPERS
    ### ------------------------------------------------------------------ ###

    def page_error(self) -> str:
        """
        Returns the validation message VAN renders, if one is visible.

        Only elements with an id count. VAN's validators are all server controls
        and render one; Highlight without an id is just styling.
        """
        for element in self.driver.find_elements(By.CSS_SELECTOR, ".Highlight[id]"):
            if element.is_displayed() and element.text.strip():
                return element.text.strip()
        return ""

    def capture(self, label: str) -> dict:
        """
        Writes a screenshot and the page source next to the run.

        A sweep of 50 districts fails unattended and VAN's state is gone by the
        time anybody looks, so the page as it stood is the only evidence there is.
        """
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
        base = os.path.join(ARTIFACT_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{stem}")

        def write_text(path, text):
            with open(path, "w") as handle:
                handle.write(text)

        written = {}

        # Whatever frame the failure happened in comes first. A save runs inside a
        # RadWindow, so switching out before reading would file the page behind the
        # popup and lose the one that actually failed.
        inner = None
        try:
            inner = self.driver.page_source
        except Exception:
            pass
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        outer = None
        try:
            outer = self.driver.page_source
        except Exception:
            pass

        for suffix, write in [
            (".png", lambda path: self.driver.save_screenshot(path)),
            (".html", lambda path: write_text(path, inner if inner else outer)),
            (".url", lambda path: write_text(path, self.driver.current_url)),
        ]:
            path = base + suffix
            try:
                write(path)
                written[suffix.lstrip(".")] = path
            except Exception as capture_error:
                # Never let the capture bury the failure that caused it.
                written[suffix.lstrip(".")] = f"not captured: {capture_error}"

        # Only when the failure was inside a frame, so there are two pages worth
        # keeping rather than the same one twice.
        if inner and outer and inner != outer:
            path = f"{base}.parent.html"
            try:
                write_text(path, outer)
                written["parent_html"] = path
            except Exception as capture_error:
                written["parent_html"] = f"not captured: {capture_error}"

        return written

    ### ------------------------------------------------------------------ ###
    ## FOLDER DETAIL AND PRINT REGION
    ### ------------------------------------------------------------------ ###

    def open_folder(self, folder_id, region_name: str = ""):
        """
        Opens a folder by id and filters its grid down to map regions.

        The grid pages at forty rows, so a big folder is filtered by type and name
        before it is read. Remember Filters is not clicked: it is a shared login.
        """
        self.driver.get(f"{FOLDER_DETAIL_URL}?FolderID=EID{van_obfuscate(folder_id)}")
        self._settle()

        controls.select_by_label(self, FOLDER_TYPE_FILTER, MAP_REGION_TYPE)

        if region_name:
            name_filter = find_element(self.driver, By.CSS_SELECTOR, FOLDER_NAME_FILTER)
            name_filter.clear()
            name_filter.send_keys(region_name)

        find_and_click(self.driver, By.CSS_SELECTOR, FOLDER_FILTER_BUTTON)
        self._settle()

    def folder_map_regions(self) -> list:
        """
        Returns the map regions on the folder page, as
        [{id, name, last_refreshed, print_url}]. Only region rows carry a Print
        Region href, and print_url is the url pulled out of its LoadLists() call.
        """
        rows = self.driver.execute_script(
            """
            const rows = [];
            for (const row of document.querySelectorAll(arguments[0])) {
                const print = row.querySelector(arguments[1]);
                if (!print) { continue; }
                const name = row.querySelector(arguments[2]);
                const cells = row.querySelectorAll('td');
                rows.push({
                    id: cells[arguments[3]] ? cells[arguments[3]].textContent.trim() : '',
                    name: name ? name.textContent.trim() : '',
                    last_refreshed: cells[arguments[4]] ? cells[arguments[4]].textContent.trim() : '',
                    print_href: print.getAttribute('href') || '',
                });
            }
            return rows;
            """,
            FOLDER_ROWS,
            FOLDER_ROW_PRINT,
            FOLDER_ROW_NAME,
            FOLDER_ROW_ID_CELL,
            FOLDER_ROW_REFRESHED_CELL,
        )

        regions = []
        for row in rows:
            match = re.search(LOAD_LISTS_URL_PATTERN, row.get("print_href", ""))
            if not match:
                continue
            row["print_url"] = match.group(1)
            regions.append(row)

        return regions

    def find_map_region(self, name: str, region_id: str = None) -> dict:
        """
        Returns the map region with this exact name. With region_id, the one with
        that id, since VAN lets two regions in a folder share a name. Without it,
        two matches is an error.
        """
        regions = self.folder_map_regions()
        matches = [region for region in regions if region["name"] == name]

        if not matches:
            raise VanUiError(
                f"No map region named {name!r} in this folder. It holds: "
                f"{', '.join(sorted(region['name'] for region in regions)) or 'no map regions'}"
            )
        if region_id:
            by_id = [region for region in matches if region["id"] == str(region_id)]
            if not by_id:
                raise VanUiError(
                    f"No map region {name!r} with id {region_id} in this folder. Its ids are: "
                    f"{', '.join(sorted(region['id'] for region in matches))}. The region "
                    "was rebuilt since the last pull, so rerun the pull and the turf build."
                )
            return by_id[0]
        if len(matches) > 1:
            raise VanUiError(
                f"{len(matches)} map regions in this folder are named {name!r}. "
                "Delete the one that is not wanted before printing."
            )

        return matches[0]

    def _go_and_wait(self, action):
        """
        Runs something that leaves the page, then waits for the new one. Follows a
        new window if one opened instead.
        """
        page = self.driver.find_element(By.TAG_NAME, "html")
        windows = set(self.driver.window_handles)
        action()
        try:
            self._wait().until(
                lambda d: len(d.window_handles) > len(windows) or staleness_of(page)(d)
            )
        except TimeoutException:
            pass
        opened = [w for w in self.driver.window_handles if w not in windows]
        if opened:
            self.driver.switch_to.window(opened[-1])
        self._settle()

    def open_print_region(self, region: dict, expected_name: str = "") -> str:
        """
        Opens the print wizard for a map region and returns its url.

        Runs the folder page's own LoadLists() with its confirm() answered yes, which
        is what clicking Print Region does minus a dialog nobody is there to answer.
        A page with no LoadLists gets the RunSearch form posted instead. If either
        lands somewhere other than the wizard, the link's address is opened directly.
        """
        landed = []

        def load_lists():
            ran = self.driver.execute_script(
                """
                if (typeof window.LoadLists !== 'function') { return false; }
                window.confirm = function () { return true; };
                window.alert = function () {};
                window.LoadLists(arguments[0]);
                return true;
                """,
                region["print_url"],
            )
            if not ran:
                form = self.driver.find_element(By.ID, RUN_SEARCH_FORM)
                self.driver.execute_script(
                    "arguments[0].action = arguments[1]; arguments[0].submit();",
                    form,
                    region["print_url"],
                )

        self._go_and_wait(load_lists)

        if PRINT_WIZARD_URL_FRAGMENT not in self.driver.current_url:
            landed.append(self.driver.current_url)
            direct = quote(urljoin(VOTEBUILDER + "/", region["print_url"]), safe=":/?&=%#")
            self._go_and_wait(lambda: self.driver.get(direct))

        if PRINT_WIZARD_URL_FRAGMENT not in self.driver.current_url:
            landed.append(self.driver.current_url)
            raise VanUiError(
                f"Print Region for {region['name']!r} landed on {' and then '.join(landed)} "
                f"rather than the print wizard."
            )

        # Prefilled with the region name, so a mismatch means the wrong region opened.
        title = find_element(self.driver, By.CSS_SELECTOR, PRINT_TITLE_INPUT).get_attribute("value")
        wanted = expected_name or region["name"]
        if title.strip() != wanted:
            raise VanUiError(
                f"The print wizard opened on {title.strip()!r}, not {wanted!r}."
            )

        return self.driver.current_url

    def print_wizard_options(self) -> dict:
        """Returns what each of the four dropdowns offers, as {field: {label: value}}."""
        return {
            "minivan_campaign": controls.options(self, PRINT_MINIVAN_CAMPAIGN_SELECT),
            "report_format": controls.options(self, PRINT_REPORT_FORMAT_SELECT),
            "script": controls.options(self, PRINT_SCRIPT_SELECT),
            "contacted_how": controls.options(self, PRINT_CONTACTED_HOW_SELECT),
        }

    def fill_print_wizard(self, choices: dict) -> dict:
        """
        Sets the four wizard dropdowns in PRINT_FIELD_ORDER and reads back what landed.

        Report Format goes first because it can postback and wipe the rest; MiniVAN
        Campaign goes last because it rewrites Script and Contacted How, which is why
        the caller gets the read-back.

        With a campaign, Script and Contacted How are left to it. The campaign sets and
        locks them, and VAN remembers the last campaign printed with, so after one real
        print they open already locked. The caller checks the script it set.

        Then the checkboxes in PRINT_CHECKBOXES, such as Print People, are ticked or
        unticked to match.
        """
        selectors = {
            "minivan_campaign": PRINT_MINIVAN_CAMPAIGN_SELECT,
            "report_format": PRINT_REPORT_FORMAT_SELECT,
            "script": PRINT_SCRIPT_SELECT,
            "contacted_how": PRINT_CONTACTED_HOW_SELECT,
        }
        campaign_sets = ("script", "contacted_how") if choices.get("minivan_campaign") else ()

        for field in PRINT_FIELD_ORDER:
            wanted = choices.get(field)
            if not wanted or field in campaign_sets:
                continue
            controls.select_by_label(self, selectors[field], wanted)
            self._settle()

        # After the dropdowns, since a report format or campaign change can reset them.
        boxes = {label: controls.set_checkbox(self, label, wanted)
                 for label, wanted in PRINT_CHECKBOXES.items()}

        applied = {field: controls.selected_label(self, selector)
                   for field, selector in selectors.items()}
        applied.update(boxes)
        return applied

    def set_print_title(self, title: str) -> str:
        """
        Types the PDF's title over the region name the wizard fills in, and returns what
        the box holds after. The title is all the PDF Print Jobs list shows of a job, so
        it is what the download finds the PDF by.
        """
        box = find_element(self.driver, By.CSS_SELECTOR, PRINT_TITLE_INPUT)

        limit = box.get_attribute("maxlength") or ""
        if limit.isdigit() and int(limit) > 0 and len(title) > int(limit):
            raise VanUiError(
                f"The title {title!r} is {len(title)} characters, and VAN's title box "
                f"takes {limit}."
            )

        box.clear()
        box.send_keys(title)

        landed = (box.get_attribute("value") or "").strip()
        if landed != title:
            raise VanUiError(f"The title box holds {landed!r} rather than {title!r}.")

        return landed

    def submit_print_wizard(self) -> str:
        """Clicks Next and returns the wait page url, or raises what VAN said instead."""
        find_and_click(self.driver, By.CSS_SELECTOR, PRINT_NEXT_BUTTON)
        self._settle()

        error = self.page_error()
        if error:
            raise VanUiError(f"The print wizard refused the job: {error}")

        if not self.driver.find_elements(By.CSS_SELECTOR, PRINT_JOB_WAIT_MARKER):
            raise VanUiError(
                f"Clicking Next landed on {self.driver.current_url} rather than the "
                "PDF wait page, so VAN did not take the print job."
            )

        return self.driver.current_url

    ### ------------------------------------------------------------------ ###
    ## PDF PRINT JOBS
    ### ------------------------------------------------------------------ ###

    def open_pdf_job_list(self):
        """Opens the first page of the PDF Print Jobs list."""
        self.driver.get(PDF_JOB_LIST_URL)
        self._settle()

    def pdf_job_rows(self) -> dict:
        """
        Reads the page of print jobs on screen in one pass, as
        {offset_minutes, offset_problem, pages, rows: [{job_id, size, title, type, created, href}]}.
        Created is left as the grid shows it, in the state's time; offset_minutes is
        that time zone, off the page's own context. A row still rendering has no href.

        offset_minutes is None, with offset_problem saying why, when the page's context
        has no usable time zone. The caller decides what to read the times as.

        The script runs strict (see USE_STRICT) and hands back one JSON string, so
        ChromeDriver never has to serialize anything off the page itself.
        """
        raw = self.driver.execute_script(
            USE_STRICT + """
            function text(cells, index) {
                return cells[index] ? cells[index].textContent.trim() : '';
            }
            const rows = [];
            const found = document.querySelectorAll(arguments[0]);
            for (let i = 0; i < found.length; i++) {
                const row = found[i];
                const id = row.querySelector(arguments[1]);
                if (!id) { continue; }
                const cells = row.querySelectorAll(':scope > td');
                const link = row.querySelector(arguments[2]);
                rows.push({
                    job_id: id.getAttribute('data-jobid') || '',
                    size: id.getAttribute('data-fs') || '',
                    title: text(cells, arguments[3]),
                    type: text(cells, arguments[4]),
                    created: text(cells, arguments[5]),
                    href: link ? (link.getAttribute('href') || '') : '',
                });
            }
            const pager = document.querySelector(arguments[6]);

            let offset = null;
            let problem = '';
            try {
                const context = (window.van && window.van.context) || {};
                let value = context.timeZoneOffset;
                if (typeof value === 'function') { value = value.call(context); }
                if (value === null || value === undefined || value === '' || !isFinite(Number(value))) {
                    problem = 'van.context.timeZoneOffset is ' + (value === null ? 'null' : typeof value);
                } else {
                    offset = Number(value);
                }
            } catch (error) {
                problem = 'reading van.context.timeZoneOffset threw ' + ((error && error.message) || error);
            }

            return JSON.stringify({
                offset_minutes: offset,
                offset_problem: problem,
                pager: pager ? pager.textContent : '',
                rows: rows,
            });
            """,
            PDF_JOB_ROWS,
            PDF_JOB_ID,
            PDF_JOB_DOWNLOAD_LINK,
            PDF_JOB_TITLE_CELL,
            PDF_JOB_TYPE_CELL,
            PDF_JOB_CREATED_CELL,
            PDF_JOB_PAGER,
        )
        listing = json.loads(raw)

        pages = re.search(PDF_JOB_PAGES_PATTERN, listing.pop("pager") or "")
        listing["pages"] = int(pages.group(1)) if pages else 1

        return listing

    def pdf_job_page(self, page: int):
        """Moves the print job list to a page, through the grid's own postback."""
        grid = find_element(self.driver, By.CSS_SELECTOR, "table#gv")
        self.driver.execute_script(
            USE_STRICT + "__doPostBack(arguments[0], arguments[1]);",
            PDF_JOB_GRID_EVENT_TARGET,
            f"Page${page}",
        )
        self._wait().until(staleness_of(grid))
        self._settle()

    def download(self, url: str) -> bytes:
        """
        Fetches a file with this browser's login, outside the browser. Chrome would
        save it to disk wherever it likes; requests hands the bytes straight back.
        """
        session = requests.Session()
        session.headers["User-Agent"] = self.driver.execute_script(
            USE_STRICT + "return navigator.userAgent;"
        )
        for cookie in self.driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"],
                                domain=cookie.get("domain"), path=cookie.get("path", "/"))

        response = session.get(url, timeout=self.timeout * 4)
        response.raise_for_status()

        return response.content