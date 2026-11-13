"""
Runs the list-number flow on one list from the Packet Tracker, saving every screen.

    python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login
    python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login --region "SL-R03-Colonie_Colonie-07"
    python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login --region "SL-R03-Colonie_Colonie-07" --commit

Without --region it takes the first list on the Packet Tracker (what the Queue picked).
VAN lets two map regions share a name, so every region on the Packet Tracker with that
name is done together, the way the search shows them. It logs in, switches into the
state, then on its own:

    1. opens My Turfs
    2. opens Filters | Edit
    3. types the list's name (SL-R03-Colonie_Colonie-07) into Name or List # and clicks Refresh
    4. ticks the box next to 'Turf', selecting every turf the search shows
    5. chooses Generate List Number from Quick Actions, and stops unless the pop-up counts
       as many map regions as the Packet Tracker has under that name
    6. clicks Next in the pop-up
    7. fills in Script, Contacted How and MiniVAN Campaign from the sheet, after checking
       the page is for that list
    8. without --commit, clicks Exit, so no list number is made
       with --commit, clicks Generate List Numbers/Next on each map region's page instead

Title and the three checkboxes are left as VAN fills them in. With --commit it refuses to
generate a page whose fields did not land as the sheet says. Each screen is saved as a
screenshot, the URL, the page and every frame in it, and every form control with its
current value. If it cannot find something, it saves the page it was stuck on and asks you
to do that one step by hand. At the end it zips everything into one file to send back.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from selenium import webdriver
from selenium.common.exceptions import (
    NoAlertPresentException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

from core_utilities.van_browser_utilities import (
    DEFAULT_ACTIONID_USER,
    MY_VOTERS,
    resolve_actionid_credentials,
)
from flows.packetland.mapped_packets import load_mapped_packets
from flows.van_automation.constants import CONTACTED_HOW, PRINT_NEXT_BUTTON
from flows.van_automation.target_slots import committee_for
from flows.van_automation import van_ui
from flows.van_automation.van_ui import ARTIFACT_DIR, VanBrowser

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## CAPTURE MY TURFS ------------------------------------------------------------------------------------------------------------------------------ ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# Same reason as USE_STRICT in van_ui: some VAN pages walk the call stack and throw on
# ChromeDriver's wrapper unless the script reading them is strict.
STRICT = "'use strict';\n"

# How deep to follow frames inside frames. VAN's popups are one frame down; two is spare.
FRAME_DEPTH = 2

# How long to let a click land before checking the page has finished loading, in seconds.
# Only the odd click that has nothing better to wait on uses it; the rest watch the page
# for what they are waiting for, every POLL_SECONDS, the way the print wizard does.
CLICK_PAUSE_SECONDS = 2
POLL_SECONDS = 0.25

# The longest any one wait goes on, in seconds, before the step is called stuck.
PAGE_WAIT_SECONDS = 30

# With SLOW_PACE on in van_ui.py, the extra pause after each page update, in seconds. At 1.5
# a list takes about as long as it did before the speedup; 0 leaves only VAN's own pace.
LIST_PACE_SECONDS = .75

# Once a search's old rows are gone (or this long has passed, if the grid redraws in place
# and they never quite go), the new rows are read.
OLD_ROWS_SECONDS = 5

# The Generate List Numbers pop-up opens saying 0 map regions and VAN fills in the real count
# a moment later, so a 0 is only believed once it has held this long, in seconds.
ZERO_COUNT_HOLD_SECONDS = 3

# How long to wait for the turf grid to show the search's results, in seconds. The grid is
# filled in after the page itself has loaded, so the page being ready is not enough.
RESULTS_WAIT_SECONDS = 30

# What can be clicked. Containers like div are left out, so a match is the control itself.
CLICKABLE = "a, button, input[type=button], input[type=submit], span, li"
BUTTONS = "a, button, input[type=button], input[type=submit]"

# The filter that narrows My Turfs, as its label reads.
NAME_FILTER_LABEL = "name or list"

# What the button that applies the filters might say, most likely first. Enter is the fallback.
APPLY_WORDS = ["refresh", "refresh results", "apply", "apply filters", "search",
               "update results", "update", "done", "filter", "ok", "save"]

# The Generate List Numbers fields, by label, in the order they are set. The campaign goes
# first: picking one sets and locks Script and Contacted How itself, as on the print wizard,
# so those two are only touched if it left them different from the sheet.
GENERATE_FIELDS = [("minivan_campaign", "minivan campaign"), ("script", "script"),
                   ("contacted_how", "contacted how")]

# The button that makes the list numbers. Only --commit clicks it. The Generate List Numbers
# page is ScriptSortOptions.aspx, the print wizard's own page, so its button is the print
# wizard's Next; the text is the fallback.
GENERATE_BUTTON = "generate list numbers/next"
GENERATE_BUTTON_CSS = PRINT_NEXT_BUTTON

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## PAGE SCRIPTS
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# Every form control on a page with what it is set to right now. Values are cut short, since
# __VIEWSTATE alone runs to hundreds of kilobytes and says nothing about the screen.
CONTROLS_SCRIPT = STRICT + r"""
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const cut = (text, n) => (text || '').replace(/\s+/g, ' ').trim().slice(0, n);
const labelOf = el => {
  if (el.id) {
    const byFor = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (byFor) return cut(byFor.textContent, 120);
  }
  const wrapping = el.closest('label');
  if (wrapping) return cut(wrapping.textContent, 120);
  const cell = el.closest('td, th, li, div');
  return cell ? cut(cell.textContent, 120) : '';
};
const controls = Array.from(document.querySelectorAll('input, select, textarea, button')).map(el => {
  const item = {
    tag: el.tagName.toLowerCase(), type: el.type || '', id: el.id || '', name: el.name || '',
    value: cut(el.value, 200), shown: shown(el), disabled: !!el.disabled, label: labelOf(el),
    title: cut(el.getAttribute('title') || el.getAttribute('data-original-title'), 200),
  };
  if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
  if (el.tagName === 'SELECT') {
    item.selected = el.selectedIndex >= 0 ? cut(el.options[el.selectedIndex].text, 200) : '';
    item.options = Array.from(el.options).slice(0, 300).map(o => cut(o.text, 200));
  }
  return item;
});
return JSON.stringify({title: document.title, url: location.href, controls: controls});
"""

# How a control is labelled on screen: its <label>, a label around it, a placeholder or
# aria-label, or failing those the text just before it. Shared by the scripts below.
LABEL_HELPERS = r"""
const shown = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
const norm = s => (s || '').replace(/\s+/g, ' ').replace(/[*:]/g, '').trim().toLowerCase();
const labelsOf = el => {
  const found = [];
  if (el.id) {
    const byFor = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (byFor) found.push(byFor.textContent);
  }
  const wrapping = el.closest('label');
  if (wrapping) found.push(wrapping.textContent);
  found.push(el.getAttribute('placeholder'), el.getAttribute('aria-label'), el.getAttribute('title'));
  let node = el;
  for (let level = 0; node && level < 3 && found.filter(Boolean).length < 4; level++) {
    let before = node.previousElementSibling;
    while (before && !norm(before.textContent)) before = before.previousElementSibling;
    if (before) { found.push(before.textContent); break; }
    node = node.parentElement;
  }
  return found.map(norm).filter(Boolean);
};
"""

# Checkboxes in VAN's newer pages can be drawn by a styled label while the real box is
# hidden, so a box counts as on screen if it or the thing drawn for it is.
BOX_HELPERS = r"""
const shown = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const proxyOf = box => {
  if (shown(box)) return box;
  if (box.id) {
    const byFor = document.querySelector('label[for="' + CSS.escape(box.id) + '"]');
    if (shown(byFor)) return byFor;
  }
  const wrapping = box.closest('label');
  if (shown(wrapping)) return wrapping;
  const next = box.nextElementSibling;
  if (shown(next) && !norm(next.textContent)) return next;
  return null;
};
const isHeaderText = text => /people/i.test(text) && /doors/i.test(text);
// Whether something in el reads exactly the word, like the 'Turf' column heading. Text runs
// together across cells ('TurfFolderPeople'), so the cells are looked at one by one.
const hasCell = (el, word) => norm(el.textContent).toLowerCase() === word
  || Array.from(el.querySelectorAll('*')).some(cell => norm(cell.textContent).toLowerCase() === word);
const namedSelectAll = box => /select.?all|chk.?all|check.?all/i.test((box.id || '') + ' ' + (box.name || ''));
// The row a box is in: a table row, an ARIA row, or failing those the nearest block
// around it holding a link and a number (a turf's name and its People and Doors).
const rowOf = box => {
  const row = box.closest('tr, [role=row]');
  if (row) return row;
  let up = box.parentElement;
  for (let level = 0; up && level < 6; level++, up = up.parentElement) {
    if (up.querySelector('a') && /\d/.test(up.textContent || '')) return up;
  }
  return null;
};
"""

# The element whose text (or value, title, aria-label) is the wanted text: the visible one
# first, then the innermost, then the one with the least other text. scope 'dialog' looks
# only inside an open pop-up, so a pager's Next can never be taken for the pop-up's.
FIND_SCRIPT = STRICT + r"""
const [wanted, exact, tags, scope, visibleOnly] = arguments;
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
const textOf = el => norm(el.tagName === 'INPUT' ? el.value : el.textContent);
const namesOf = el => [textOf(el), norm(el.getAttribute('title')),
  norm(el.getAttribute('aria-label')), norm(el.getAttribute('data-original-title'))];
const roots = scope === 'dialog'
  ? Array.from(document.querySelectorAll(
      '.modal, [role=dialog], .ui-dialog, .RadWindow, .rwDialogPopup')).filter(shown)
  : [document];
const hits = [];
for (const root of roots) {
  for (const el of root.querySelectorAll(tags)) {
    if (visibleOnly && !shown(el)) continue;
    const match = namesOf(el).some(n => n && (exact ? n === wanted : n.includes(wanted)));
    if (match && !hits.includes(el)) hits.push(el);
  }
}
// The innermost match: a menu item's li is as wide as the menu, so a click in its middle
// can miss the link inside it that actually does something.
const innermost = hits.filter(el => !hits.some(other => other !== el && el.contains(other)));
innermost.sort((a, b) => (shown(b) - shown(a)) || (textOf(a).length - textOf(b).length));
return innermost[0] || null;
"""

# The Edit in 'Filters | Edit': an Edit with 'filter' in the text just around it.
FILTER_EDIT_SCRIPT = STRICT + r"""
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
for (const el of document.querySelectorAll('a, button, span, input[type=button]')) {
  const text = norm(el.tagName === 'INPUT' ? el.value : el.textContent);
  if (text !== 'edit' || !shown(el)) continue;
  let up = el.parentElement;
  for (let level = 0; up && level < 4; level++, up = up.parentElement) {
    if (norm(up.textContent).includes('filter')) return el;
  }
}
return null;
"""

# The visible text box whose label starts with the wanted words.
INPUT_BY_LABEL_SCRIPT = STRICT + LABEL_HELPERS + r"""
const wanted = arguments[0];
for (const el of document.querySelectorAll('input, textarea')) {
  if (!['text', 'search', ''].includes((el.getAttribute('type') || '').toLowerCase())) continue;
  if (!shown(el) || el.disabled) continue;
  if (labelsOf(el).some(label => label.startsWith(wanted))) return el;
}
return null;
"""

# The button that applies a filter box: the nearest one around the box that says one of the
# words, so a Search link elsewhere on the page is never taken for it.
APPLY_NEAR_SCRIPT = STRICT + r"""
const [box, words] = arguments;
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
let up = box.parentElement;
for (let level = 0; up && level < 10; level++, up = up.parentElement) {
  const buttons = Array.from(up.querySelectorAll('a, button, input[type=button], input[type=submit]'))
    .filter(shown);
  for (const word of words) {
    for (const el of buttons) {
      if (norm(el.tagName === 'INPUT' ? el.value : el.textContent) === word) return [el, word];
    }
  }
}
return null;
"""

# The visible dropdown whose label starts with the wanted words.
SELECT_BY_LABEL_SCRIPT = STRICT + LABEL_HELPERS + r"""
const wanted = arguments[0];
for (const el of document.querySelectorAll('select')) {
  if (!shown(el)) continue;
  if (labelsOf(el).some(label => label.startsWith(wanted))) return el;
}
return null;
"""

# Every turf row in the grid with what it is called. A row's name is its first link plus
# any tooltip in it, since VAN cuts long names short. Bold marks a map region; the turfs
# under it are not bold. The header row and the filter panel are left out.
GRID_ROWS_SCRIPT = STRICT + BOX_HELPERS + r"""
const regionId = arguments[0];
const rows = [];
const seen = new Set();
for (const box of document.querySelectorAll('input[type=checkbox]')) {
  if (!proxyOf(box) || namedSelectAll(box)) continue;
  const row = rowOf(box);
  if (!row || seen.has(row) || row.closest('thead') || row.querySelector('th, [role=columnheader]')) continue;
  const text = norm(row.textContent);
  if (isHeaderText(text) || !/\d/.test(text)) continue;
  const nameEl = row.querySelector('a');
  if (!nameEl || !norm(nameEl.textContent)) continue;
  seen.add(row);
  const names = [norm(nameEl.textContent)];
  for (const el of row.querySelectorAll('[title], [data-original-title]')) {
    names.push(norm(el.getAttribute('data-original-title') || el.getAttribute('title')));
  }
  const html = row.outerHTML;
  rows.push({
    box: box,
    checked: box.checked,
    // What the row shows, cell by cell: Last Out/Distributed has a list number once one is made.
    text: (row.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 600),
    names: names.filter(Boolean),
    bold: parseInt(getComputedStyle(nameEl).fontWeight, 10) >= 600,
    has_id: !!regionId && new RegExp('(^|\\D)' + regionId + '(\\D|$)').test(html),
    html: html.slice(0, 4000),
  });
}
return rows;
"""

# The tick-everything box in the grid's header, next to 'Turf'. Passed the turf rows' own
# boxes, so none of those is taken for it.
SELECT_ALL_BOX_SCRIPT = STRICT + BOX_HELPERS + r"""
const rowBoxes = new Set(arguments[0] || []);
let fallback = null;
for (const box of document.querySelectorAll('input[type=checkbox]')) {
  if (rowBoxes.has(box) || !proxyOf(box)) continue;
  const row = box.closest('tr, [role=row]');
  const inHeader = !!(box.closest('thead') || (row && row.querySelector('th, [role=columnheader]'))
                      || (row && isHeaderText(row.textContent || '')));
  // Or a box with a 'Turf' heading just beside it and no numbers: the header, not a turf's row.
  let besideTurf = false;
  let up = box.parentElement;
  for (let level = 0; up && level < 4; level++, up = up.parentElement) {
    const text = norm(up.textContent);
    if (text.length > 300 || /\d/.test(text)) break;
    if (hasCell(up, 'turf')) { besideTurf = true; break; }
  }
  if (!inHeader && !besideTurf && !namedSelectAll(box)) continue;
  if (besideTurf || (row && hasCell(row, 'turf'))) return box;
  fallback = fallback || box;
}
return fallback;
"""

# What to click to tick a box that is drawn by something else.
BOX_PROXY_SCRIPT = STRICT + BOX_HELPERS + r"""
return proxyOf(arguments[0]);
"""

# A visible dropdown list that offers Generate List Number, if Quick Actions is one.
QUICK_ACTIONS_SELECT_SCRIPT = STRICT + r"""
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
for (const select of document.querySelectorAll('select')) {
  if (!shown(select)) continue;
  for (const option of select.options) {
    if (/generate list number/i.test(option.text)) return [select, option.text];
  }
}
return null;
"""

# The text of whatever pop-up is open, or failing that of the whole page.
POPUP_TEXT_SCRIPT = STRICT + r"""
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const popups = Array.from(document.querySelectorAll('.modal, [role=dialog], .ui-dialog, .RadWindow'))
  .filter(shown).map(el => el.innerText).join(' | ');
return /map region/i.test(popups) ? popups : (document.body ? document.body.innerText : '');
"""

# The Next beside 'Generate List Numbers for ...': a visible Next with that text close
# around it, so the grid's own pager can never be taken for it.
POPUP_NEXT_SCRIPT = STRICT + r"""
const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
for (const el of document.querySelectorAll('a, button, input[type=button], input[type=submit]')) {
  if (!shown(el) || norm(el.tagName === 'INPUT' ? el.value : el.textContent) !== 'next') continue;
  let up = el.parentElement;
  for (let level = 0; up && level < 8; level++, up = up.parentElement) {
    const text = norm(up.textContent);
    if (text.length > 3000) break;
    if (text.includes('generate list numbers for')) return el;
  }
}
return null;
"""

# The page's heading for the map region in hand: 'Map Region 1 of 2 (SL-R03-...)'.
REGION_HEADING_SCRIPT = STRICT + r"""
const text = document.body ? document.body.innerText : '';
const found = text.match(/Map Region\s+(\d+)\s+of\s+(\d+)\s*\(([^)\n]*)\)/i);
const total = text.match(/Generate\s+(\d+)\s+List Numbers?/i);
return found ? [parseInt(found[1], 10), parseInt(found[2], 10), found[3].trim(),
                total ? parseInt(total[1], 10) : null] : null;
"""

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## SAVING WHAT IS ON SCREEN
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def write(path: str, text: str):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def save_document(driver, base: str, saved: list):
    """Saves the page or frame the driver is in now: its source and its controls."""
    try:
        write(base + ".html", driver.page_source)
        saved.append(base + ".html")
    except Exception as error:
        write(base + ".html.error.txt", str(error))

    try:
        controls = json.loads(driver.execute_script(CONTROLS_SCRIPT))
        write(base + ".controls.json", json.dumps(controls, indent=2))
        saved.append(base + ".controls.json")
    except Exception as error:
        write(base + ".controls.error.txt", str(error))

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def save_frames(driver, base: str, saved: list, depth: int = 1):
    """Saves every frame in the current document, then the frames inside those."""
    if depth > FRAME_DEPTH:
        return

    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for number in range(1, len(frames) + 1):
        # Found again each time: switching back to the parent can leave the old list stale.
        current = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if number > len(current):
            break
        frame_base = f"{base}.frame{number}"
        try:
            driver.switch_to.frame(current[number - 1])
        except Exception as error:
            write(frame_base + ".error.txt", str(error))
            continue
        try:
            save_document(driver, frame_base, saved)
            save_frames(driver, frame_base, saved, depth + 1)
        finally:
            driver.switch_to.parent_frame()

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def save_screen(driver, folder: str, index, name: str) -> list:
    """
    Saves everything on screen as NN_name.*, in every open tab. Returns the files written.

    Every tab, because a VAN action can open its page in a new one, and the frames in each,
    because VAN's popups are pages of their own inside the one you see.

    Never raises: saving the evidence must not be what stops a run. A list name can hold a
    '/' (SL-R05-Deerpark_Portjervis/City-001-02), so the file name keeps only safe characters.
    """
    saved = []
    prefix = f"{index:02d}" if isinstance(index, int) else str(index)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_") or "page"
    try:
        start = driver.current_window_handle
        handles = driver.window_handles
    except Exception:
        return saved

    for tab, handle in enumerate(handles, start=1):
        base = os.path.join(folder, f"{prefix}_{safe_name}")
        if len(handles) > 1:
            base += f".tab{tab}"
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
            write(base + ".url.txt", driver.current_url)
            driver.save_screenshot(base + ".png")
            saved.append(base + ".png")
            save_document(driver, base, saved)
            save_frames(driver, base, saved)
        except Exception as error:
            try:
                write(base + ".error.txt", str(error))
            except Exception:
                pass

    try:
        driver.switch_to.window(start)
        driver.switch_to.default_content()
    except Exception:
        pass
    return saved

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## FINDING AND CLICKING
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

class Stuck(Exception):
    """The step could not find what it needed on the page, or found something wrong."""

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def find(driver, wanted: str, tags: str = CLICKABLE, exact: bool = True,
         scope: str = "page", visible: bool = True):
    """The element showing this text, or None."""
    return driver.execute_script(FIND_SCRIPT, wanted.lower(), exact, tags, scope, visible)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def find_in_frames(driver, attempt, depth: int = 1):
    """
    Runs attempt(top) in the page, then in each visible frame, until it finds something.
    Leaves the driver in the frame it was found in, so it can be used there.
    """
    found = attempt(depth == 1)
    if found or depth > FRAME_DEPTH:
        return found

    count = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
    for number in range(count):
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if number >= len(frames) or not frames[number].is_displayed():
            continue
        driver.switch_to.frame(frames[number])
        found = find_in_frames(driver, attempt, depth + 1)
        if found:
            return found
        driver.switch_to.parent_frame()
    return None

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def click(driver, element, allow_generate: bool = False):
    """
    Clicks it, by JavaScript if Selenium cannot. Refuses Generate List Numbers/Next unless
    allow_generate, which only --commit passes.
    """
    label = (element.text or element.get_attribute("value") or "").strip().lower()
    if GENERATE_BUTTON in label and not allow_generate:
        raise Stuck(f"refused to click {label!r}: that would make list numbers")

    driver.execute_script(STRICT + "arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
    except Exception:
        driver.execute_script(STRICT + "arguments[0].click();", element)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def click_box(driver, box):
    """Ticks a checkbox, through the thing drawn for it when the real box is hidden."""
    if box.is_displayed():
        click(driver, box)
        return
    proxy = driver.execute_script(BOX_PROXY_SCRIPT, box)
    if proxy:
        click(driver, proxy)
    else:
        driver.execute_script(STRICT + "arguments[0].click();", box)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def answer_alert(driver, accept: bool) -> str:
    """Closes a browser alert if one is up, and returns what it said."""
    try:
        alert = driver.switch_to.alert
    except NoAlertPresentException:
        return ""
    text = alert.text
    alert.accept() if accept else alert.dismiss()
    return text

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def wait_for_page(browser, pause: float = CLICK_PAUSE_SECONDS):
    """Gives a click time to land, then waits until the page has finished loading."""
    time.sleep(pause)
    browser.driver.switch_to.default_content()
    browser._settle()


def settle(browser):
    """
    Waits until the page has loaded and no background update is in flight. With SLOW_PACE
    on (van_ui.py), it then pauses LIST_PACE_SECONDS more, so a list takes about as long as
    it did before the speedup.
    """
    browser.driver.switch_to.default_content()
    browser._settle()
    if van_ui.SLOW_PACE:
        time.sleep(LIST_PACE_SECONDS)


def wait_until(check, timeout: float = PAGE_WAIT_SECONDS):
    """
    Calls check() every POLL_SECONDS until it returns something, or the time runs out, and
    returns what it last returned. A page halfway through loading can fail a look; that look
    just counts as nothing yet. So does an alert that pops up mid-look: alerts are left open
    (see quiet_driver), so the checks that answer them get them on the next look.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = check()
        except (StaleElementReferenceException, WebDriverException):
            result = None
        if result or time.monotonic() > deadline:
            return result
        time.sleep(POLL_SECONDS)


def is_stale(element) -> bool:
    """Whether the element has gone from the page, as it does when the page is redrawn."""
    try:
        element.is_enabled()
        return False
    except StaleElementReferenceException:
        return True

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## MY TURFS
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def shortened(text: str):
    """What is left of a name VAN cut short, or None if it was not cut."""
    for ending in ("…", "..."):
        if text.endswith(ending):
            return text[:-len(ending)].rstrip()
    return None


def could_be(names: list, wanted: str) -> bool:
    """Whether a row named any of these could be the wanted name, allowing for VAN's cuts."""
    wanted = wanted.lower()
    for name in names:
        if wanted in name.lower():
            return True
        cut = shortened(name)
        if cut and wanted.startswith(cut.lower()):
            return True
    return False


def is_exactly(names: list, wanted: str) -> bool:
    """Whether a row is the wanted name, or as much of it as VAN showed."""
    for name in names:
        if name == wanted:
            return True
        cut = shortened(name)
        if cut and wanted.startswith(cut):
            return True
    return False

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def grid_rows(driver, region_id: str = "") -> list:
    return driver.execute_script(GRID_ROWS_SCRIPT, str(region_id))


# A list number as My Turfs shows it under Last Out/Distributed: '61375184-61654 Generated:
# 10/15/26 9:15 AM'. Region names carry short numbers (82-046), never six digits and a dash.
LIST_NUMBER = re.compile(r"(?<![\w-])\d{6,}-\d{2,}(?![\w-])|\bGenerated:")


def has_list_number(row: dict) -> bool:
    """Whether a grid row shows a list number already."""
    return bool(LIST_NUMBER.search(row.get("text") or ""))


def already_numbered(rows: list) -> bool:
    """
    Whether every turf the search shows already has a list number. The turfs are the rows
    that are not bold; a region's own row carries no number of its own.
    """
    turfs = [row for row in rows if not row["bold"]] or rows
    return bool(turfs) and all(has_list_number(row) for row in turfs)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def grid_signature(rows: list) -> tuple:
    """What the grid shows, to tell a new search's rows from the last one's."""
    return tuple(tuple(r["names"][:1]) for r in rows)


def wait_for_results(driver, text: str, before: dict = None) -> list:
    """
    The grid's rows once it shows the search's results: the rows from before the search are
    gone, every region (or, where bold does not tell regions apart, some row) could be the
    name searched for, and the count has held for two looks in a row. Returns whatever is
    there when the wait runs out.

    before is the grid as it was when Refresh was clicked ({'box', 'signature'}), so rows
    still left over from the last list are never read as this one's.
    """
    deadline = time.monotonic() + RESULTS_WAIT_SECONDS
    old_box = (before or {}).get("box")
    old_signature = (before or {}).get("signature")
    old_until = time.monotonic() + OLD_ROWS_SECONDS
    previous = None
    rows = []
    while True:
        try:
            if old_box is not None and is_stale(old_box):
                old_box = None
            rows = grid_rows(driver)
        except UnexpectedAlertPresentException:
            raise
        except WebDriverException:
            rows = []

        still_old = (old_box is not None and grid_signature(rows) == old_signature
                     and time.monotonic() < old_until)
        regions = [r for r in rows if r["bold"]]
        if regions:
            matches = all(could_be(r["names"], text) for r in regions)
        else:
            matches = any(could_be(r["names"], text) for r in rows)
        ready = rows and matches and not still_old
        if ready and len(rows) == previous:
            return rows
        previous = len(rows) if ready else None
        if time.monotonic() > deadline:
            return rows
        time.sleep(POLL_SECONDS)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def apply_name_filter(browser, text: str) -> tuple:
    """
    Types text into Name or List # and applies it, opening Filters | Edit if it is shut.
    Returns (how it applied it, the grid as it was just before), for wait_for_results.
    """
    driver = browser.driver

    def name_box(top):
        return driver.execute_script(INPUT_BY_LABEL_SCRIPT, NAME_FILTER_LABEL)

    box = find_in_frames(driver, name_box)
    if not box:
        driver.switch_to.default_content()
        edit = driver.execute_script(FILTER_EDIT_SCRIPT)
        if not edit:
            raise Stuck("there is no Edit next to Filters")
        click(driver, edit)
        box = wait_until(lambda: find_in_frames(driver, name_box), timeout=10)
    if not box:
        driver.switch_to.default_content()
        raise Stuck("the filters have no 'Name or List #' box")

    driver.execute_script(STRICT + "arguments[0].value = '';", box)
    box.send_keys(text)
    time.sleep(0.2)

    # The grid as it stands, so the wait after Refresh can tell when it has changed.
    old_rows = grid_rows(driver)
    before = {"box": old_rows[0]["box"] if old_rows else None,
              "signature": grid_signature(old_rows)}

    found = driver.execute_script(APPLY_NEAR_SCRIPT, box, APPLY_WORDS)
    if found:
        button, word = found
        click(driver, button)
        how = f"clicked {word!r}"
    else:
        box.send_keys(Keys.ENTER)
        how = "pressed Enter"

    time.sleep(POLL_SECONDS)
    alert = answer_alert(driver, accept=False)
    if alert:
        raise Stuck(f"after Refresh, VAN said {alert!r}")
    settle(browser)
    return how, before

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## THE STEPS
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def open_my_turfs(browser, notes: dict) -> str:
    driver = browser.driver
    link = (find(driver, "my turfs", tags="a", visible=False)
            or find(driver, "my turfs", tags="a", exact=False, visible=False))
    if not link:
        raise Stuck("there is no My Turfs link on this page")

    href = link.get_attribute("href") or ""
    if href.lower().startswith("http"):
        driver.get(href)
        settle(browser)
    else:
        click(driver, link)
        wait_for_page(browser)
    notes["my_turfs_url"] = driver.current_url
    return driver.current_url


def open_filters(browser, notes: dict) -> str:
    driver = browser.driver
    edit = driver.execute_script(FILTER_EDIT_SCRIPT)
    if not edit:
        raise Stuck("there is no Edit next to Filters")
    click(driver, edit)
    wait_until(lambda: driver.execute_script(INPUT_BY_LABEL_SCRIPT, NAME_FILTER_LABEL), timeout=10)
    settle(browser)
    return ""


def search_for_list(browser, notes: dict) -> str:
    """Types the list's name into Name or List #, clicks Refresh, and waits for the results."""
    driver = browser.driver
    name = notes["name"]
    how, before = apply_name_filter(browser, name)

    rows = wait_for_results(driver, name, before)
    notes["search"] = {"text": name, "applied_by": how, "rows_shown": len(rows),
                       "rows": [{"names": r["names"], "bold": r["bold"], "checked": r["checked"],
                                 "html": r["html"]} for r in rows[:20]]}
    if not rows:
        raise Stuck(f"after searching {name!r} ({how}) the grid shows no turf rows that the "
                    f"script can read, even after {RESULTS_WAIT_SECONDS} seconds")

    regions = [r for r in rows if r["bold"]]
    if regions and not all(could_be(r["names"], name) for r in regions):
        listed = "; ".join(r["names"][0] for r in regions[:6])
        raise Stuck(f"after searching {name!r} the grid still shows other lists: {listed}")

    told = f", {len(regions)} region{'s' if len(regions) != 1 else ''}" if regions else ""
    return f"searched {name!r} ({how}): {len(rows)} rows shown{told}"


def select_turfs(browser, notes: dict) -> str:
    """
    Ticks the box next to 'Turf', which ticks every turf the search shows.

    VAN's search matches the start of a name, so Colonie-07 also brings up a Colonie-070 if
    there is one. When the search shows a region that is not on the Packet Tracker, only the
    Packet Tracker's regions are ticked, found by the region id in their row.
    """
    driver = browser.driver
    name, wanted = notes["name"], notes["rows"]
    rows = grid_rows(driver)
    regions = [r for r in rows if r["bold"]]
    extra = [r for r in regions if not is_exactly(r["names"], name)]
    # Only the Packet Tracker's regions, by id, when the search shows any other region: a
    # look-alike name, or a region sharing this name that the Packet Tracker does not have.
    by_id = bool(len(regions) > len(wanted) or extra)

    if by_id:
        ticked_by = []
        for row in wanted:
            mine = [r for r in grid_rows(driver, row["map_region_id"]) if r["bold"] and r["has_id"]]
            if len(mine) != 1:
                listed = "; ".join(r["names"][0] for r in regions[:6])
                raise Stuck(f"the search shows {len(regions)} regions ({listed}) but the Packet "
                            f"Tracker has {len(wanted)} named {name!r}, and region "
                            f"{row['map_region_id']} cannot be told apart from the others")
            if not mine[0]["checked"]:
                click_box(driver, mine[0]["box"])
                region_id = row["map_region_id"]
                wait_until(lambda: any(r["checked"] for r in grid_rows(driver, region_id)
                                       if r["has_id"] and r["bold"]), timeout=5)
            ticked_by.append(row["map_region_id"])
        how = f"ticked only regions {', '.join(ticked_by)}, since the search also showed others"
    else:
        box = driver.execute_script(SELECT_ALL_BOX_SCRIPT, [r["box"] for r in rows])
        if box:
            # Left ticked from the list before, with the new rows unticked: untick it first.
            if box.get_property("checked") and not all(r["checked"] for r in rows):
                click_box(driver, box)
                time.sleep(0.5)
            if not box.get_property("checked"):
                click_box(driver, box)
            how = "ticked the box next to 'Turf'"
        else:
            link = find(driver, "select all", tags="a, button, span", exact=False)
            if not link or not re.search(r"select all \d+ turfs?", (link.text or "").lower()):
                raise Stuck("there is no box next to 'Turf' and no 'Select All' link")
            click(driver, link)
            how = f"clicked {link.text.strip()!r}"

    # Read again until it holds: ticking can redraw the grid, and the turfs under a region
    # can take a moment to follow it.
    def all_ticked():
        current = grid_rows(driver)
        done = [r for r in current if r["checked"]]
        return current if done and (by_id or len(done) == len(current)) else None

    rows = wait_until(all_ticked, timeout=5) or grid_rows(driver)
    ticked = [r for r in rows if r["checked"]]
    settle(browser)

    if not ticked:
        raise Stuck(f"{how}, but no box stayed ticked")
    if by_id:
        # Every Packet Tracker region ticked, and no other region.
        for row in wanted:
            mine = [r for r in grid_rows(driver, row["map_region_id"]) if r["bold"] and r["has_id"]]
            if not mine or not mine[0]["checked"]:
                raise Stuck(f"{how}, but region {row['map_region_id']} did not stay ticked")
        ticked_regions = [r for r in grid_rows(driver) if r["bold"] and r["checked"]]
        if len(ticked_regions) != len(wanted):
            raise Stuck(f"{how}, but {len(ticked_regions)} regions are ticked, not {len(wanted)}")
    elif len(ticked) < len(rows):
        raise Stuck(f"{how}, but only {len(ticked)} of {len(rows)} turfs are ticked")

    notes["ticked"] = [r["names"][0] if r["names"] else "?" for r in ticked]
    return f"{how}: {len(ticked)} of {len(rows)} rows ticked"


def choose_generate(browser, notes: dict) -> str:
    driver = browser.driver
    found = driver.execute_script(QUICK_ACTIONS_SELECT_SCRIPT)
    if found:
        select, option_text = found
        Select(select).select_by_visible_text(option_text)
    else:
        button = (find(driver, "quick actions")
                  or find(driver, "quick actions", tags="a, button, span", exact=False))
        if not button:
            raise Stuck("there is no Quick Actions")
        click(driver, button)
        item = wait_until(lambda: (
            find(driver, "generate list number", tags="a, li, button, span")
            or find(driver, "generate list number", tags="a, li, button, span", exact=False)),
            timeout=5)
        if not item:
            raise Stuck("Quick Actions has no Generate List Number")
        click(driver, item)

    # Wait for the pop-up's count of map regions (or for VAN to object). The pop-up opens
    # saying 0 and VAN counts a moment later, so a 0 has to hold before it is believed.
    first_zero = []

    def popup_or_alert():
        said = answer_alert(driver, accept=False)
        if said:
            return ("alert", said)
        shown = driver.execute_script(POPUP_TEXT_SCRIPT) or ""
        count = re.search(r"(\d[\d,]*)\s+Map Regions?", shown, re.I)
        if not count:
            return None
        if int(count.group(1).replace(",", "")) > 0:
            return ("popup", shown)
        first_zero.append(time.monotonic())
        return ("popup", shown) if first_zero[-1] - first_zero[0] >= ZERO_COUNT_HOLD_SECONDS else None

    found = wait_until(popup_or_alert, timeout=15)
    if found and found[0] == "alert":
        raise Stuck(f"VAN said {found[1]!r}")
    settle(browser)

    # Read once more now the page is still, in case the count changed while it settled.
    text = driver.execute_script(POPUP_TEXT_SCRIPT) or ""
    if not re.search(r"\d[\d,]*\s+Map Regions?", text, re.I) and found:
        text = found[1]
    counted = re.search(r"(\d[\d,]*)\s+Map Regions?", text, re.I)
    if not counted:
        def frame_text(top):
            return None if top else driver.execute_script(
                STRICT + "return document.body ? document.body.innerText : '';")
        text = find_in_frames(driver, frame_text) or ""
        driver.switch_to.default_content()
        counted = re.search(r"(\d[\d,]*)\s+Map Regions?", text, re.I)

    popup = re.search(r"Generate List Numbers? for[^|\n]*", text, re.I)
    notes["popup_text"] = " ".join((popup.group(0) if popup else text).split())[:300]

    # The last check before anything could be generated: as many map regions as the
    # Packet Tracker has under this name.
    if not counted:
        raise Stuck(f"the pop-up does not say how many map regions: {notes['popup_text'][:160]!r}")
    expected = len(notes["rows"])
    if int(counted.group(1).replace(",", "")) != expected:
        raise Stuck(f"the pop-up says {counted.group(0)}, but the Packet Tracker has {expected} "
                    f"named {notes['name']!r}; nothing was clicked in it")
    return f"the pop-up says: {notes['popup_text'][:160]}"


def click_next_in_popup(browser, notes: dict) -> str:
    driver = browser.driver
    button = driver.execute_script(POPUP_NEXT_SCRIPT) or find_in_frames(driver, lambda top: find(
        driver, "next", tags=BUTTONS, scope="dialog" if top else "page"))
    if not button:
        driver.switch_to.default_content()
        raise Stuck("there is no Next button in the pop-up")
    click(driver, button)
    said = answer_alert(driver, accept=False)
    if said:
        raise Stuck(f"after Next, VAN said {said!r}")
    driver.switch_to.default_content()

    def heading_or_alert():
        said = answer_alert(driver, accept=False)
        if said:
            return ("alert", said)
        return ("page", None) if driver.execute_script(REGION_HEADING_SCRIPT) else None

    found = wait_until(heading_or_alert)
    if found and found[0] == "alert":
        raise Stuck(f"after Next, VAN said {found[1]!r}")
    if not found:
        raise Stuck("clicked Next, but no page with 'Map Region 1 of N' came up")
    settle(browser)
    return ""

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def fill_region_page(browser, notes: dict) -> dict:
    """
    Fills one map region's page from the sheet and returns what happened. Checks first that
    the page is for this list: the heading names the region, and it has to be the one
    searched for.
    """
    driver = browser.driver
    heading = driver.execute_script(REGION_HEADING_SCRIPT)
    if not heading:
        raise Stuck("this page has no 'Map Region N of M' heading")
    number, of, page_name, total = heading
    if page_name != notes["name"]:
        raise Stuck(f"map region {number} of {of} is {page_name!r}, not {notes['name']!r}")

    # Regions sharing a name cannot be told apart on this page, so they must share settings.
    settings = {(row.get("script"), row.get("minivan_campaign")) for row in notes["rows"]}
    if len(settings) > 1:
        raise Stuck(f"the regions named {page_name!r} have different scripts or campaigns on "
                    "the sheet, and this page does not say which one it is")
    row = notes["rows"][0]
    wanted = {"script": row.get("script"), "contacted_how": CONTACTED_HOW,
              "minivan_campaign": row.get("minivan_campaign")}

    def dropdown(label):
        found = find_in_frames(driver, lambda top: driver.execute_script(SELECT_BY_LABEL_SCRIPT, label))
        return found

    landed, offered = {}, {}
    for field, label in GENERATE_FIELDS:
        select = dropdown(label)
        if not select:
            driver.switch_to.default_content()
            continue
        options = [option.text.strip() for option in Select(select).options]
        current = Select(select).first_selected_option.text.strip()
        if wanted[field] and wanted[field] not in options:
            offered[field] = options[:60]
        elif wanted[field] and current != wanted[field] and select.is_enabled():
            # Each pick can update the page, so it settles before the next field is read.
            Select(select).select_by_visible_text(wanted[field])
            settle(browser)
        driver.switch_to.default_content()

    for field, label in GENERATE_FIELDS:
        select = dropdown(label)
        landed[field] = (Select(select).first_selected_option.text.strip() if select
                         else f"no dropdown labelled {label!r}")
        driver.switch_to.default_content()

    title = find_in_frames(driver, lambda top: driver.execute_script(INPUT_BY_LABEL_SCRIPT, "title"))
    landed["title"] = title.get_attribute("value") if title else "no Title box"
    driver.switch_to.default_content()

    problems = [f"{field} is {landed[field]!r}, not {wanted[field]!r}"
                for field in wanted if wanted[field] and landed.get(field) != wanted[field]]
    problems += [f"{field} {wanted[field]!r} is not offered; it has {', '.join(options[:12])}"
                 for field, options in offered.items()]
    return {"page": f"{number} of {of}", "region": page_name, "list_numbers_on_page": total,
            "wanted": wanted, "landed": landed, "problems": problems}


def fill_generate_page(browser, notes: dict) -> str:
    """Rehearsal: fills the first map region's page from the sheet, and leaves it unsent."""
    result = fill_region_page(browser, notes)
    notes.setdefault("pages", []).append(result)
    if result["problems"]:
        return "filled, but " + "; ".join(result["problems"])
    return f"map region {result['page']} filled in as the sheet says"


def click_exit(browser, notes: dict) -> str:
    driver = browser.driver
    button = find_in_frames(driver, lambda top: find(driver, "exit", tags=BUTTONS))
    if not button:
        driver.switch_to.default_content()
        raise Stuck("there is no Exit button")
    click(driver, button)
    # VAN may ask first; with the question up, even switching frames would fail.
    said = [text for text in [answer_alert(driver, accept=True)] if text]
    driver.switch_to.default_content()

    # Gone when the page no longer has a map region heading.
    def left_page():
        text = answer_alert(driver, accept=True)
        if text:
            said.append(text)
            return None
        return not driver.execute_script(REGION_HEADING_SCRIPT)

    wait_until(left_page, timeout=15)
    settle(browser)
    return f"an alert said {said[0]!r} and was accepted" if said else ""


def generate_button(driver):
    """The Generate List Numbers/Next button: by the print wizard's id, else by its text."""
    for element in driver.find_elements(By.CSS_SELECTOR, GENERATE_BUTTON_CSS):
        if element.is_displayed():
            return element
    return (find(driver, GENERATE_BUTTON, tags=BUTTONS)
            or find(driver, "generate list numbers", tags=BUTTONS, exact=False))


def settle_through_alerts(browser, said: list):
    """Waits for the page, accepting any alert VAN puts up on the way and noting what it said."""
    for _ in range(4):
        text = answer_alert(browser.driver, accept=True)
        if text:
            said.append(text)
        try:
            settle(browser)
            text = answer_alert(browser.driver, accept=True)
            if not text:
                return
            said.append(text)
        except UnexpectedAlertPresentException:
            continue


def generate_each_region(browser, notes: dict) -> str:
    """
    --commit: fills each map region's page and clicks Generate List Numbers/Next, one page
    after another. A page whose fields did not land as the sheet says is not generated: the
    run stops there, with that page saved. So does a click that leaves the page where it was.
    """
    driver = browser.driver
    done = notes.setdefault("generated", [])
    for _ in range(len(notes["rows"]) + 1):
        heading = driver.execute_script(REGION_HEADING_SCRIPT)
        if not heading:
            break
        result = fill_region_page(browser, notes)
        notes.setdefault("pages", []).append(result)
        notes["save"](f"region_{len(done) + 1}_filled")
        if result["problems"]:
            raise Stuck(f"map region {result['page']} not generated: " + "; ".join(result["problems"]))

        button = generate_button(driver)
        if not button:
            raise Stuck("there is no Generate List Numbers/Next button")
        error_before = browser.page_error()
        click(driver, button, allow_generate=True)

        # Done when the page has moved on: the next map region, or off the page altogether.
        # VAN may ask to confirm first, and is answered yes. A new error message on the page
        # means it refused, so that ends the wait too, rather than running it out.
        said = []

        def moved_on():
            text = answer_alert(driver, accept=True)
            if text:
                said.append(text)
                return None
            now = driver.execute_script(REGION_HEADING_SCRIPT)
            if not now or now[:2] != heading[:2]:
                return "moved"
            error = browser.page_error()
            return "refused" if error and error != error_before else None

        wait_until(moved_on)
        settle_through_alerts(browser, said)
        if said:
            result["van_said"] = said

        after = driver.execute_script(REGION_HEADING_SCRIPT)
        if after and after[:2] == heading[:2]:
            error = browser.page_error()
            told = "; ".join(said + ([error] if error else []))
            raise Stuck(f"clicked Generate List Numbers/Next, but VAN is still on map region "
                        f"{result['page']}" + (f": {told}" if told else ""))
        done.append(result["page"])
        print(f"    Generated map region {result['page']}"
              + (f" (VAN said: {'; '.join(said)})" if said else ""), flush=True)

    if not done:
        raise Stuck("there was no map region page to generate")
    return f"generated map region {', '.join(done)}"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# (file name, what it does, what to do by hand if it cannot, the step)
COMMON_STEPS = [
    ("my_turfs", "Opening My Turfs", "Open My Turfs, with no filters", open_my_turfs),
    ("filters", "Opening Filters | Edit", "Click 'Filters | Edit'", open_filters),
    ("search", "Searching Name or List # for the list and clicking Refresh",
     "Type the list's name in Name or List # and click Refresh", search_for_list),
    ("ticked", "Ticking the box next to 'Turf' to select every turf shown",
     "Tick the box next to 'Turf' so every turf shown is selected", select_turfs),
    ("popup", "Choosing Generate List Number from Quick Actions",
     "Choose Generate List Number from Quick Actions", choose_generate),
    ("generate_page", "Clicking Next in the pop-up", "Click Next in the pop-up",
     click_next_in_popup),
]
REHEARSAL_STEPS = COMMON_STEPS + [
    ("generate_filled", "Filling in Script, Contacted How and MiniVAN Campaign from the sheet",
     "Fill in Script, Contacted How and MiniVAN Campaign (do NOT click Generate)",
     fill_generate_page),
    ("after_exit", "Clicking Exit (not Generate)",
     "Click Exit on the Generate List Numbers page (NOT Generate)", click_exit),
]
COMMIT_STEPS = COMMON_STEPS + [
    ("after_generate", "Filling in each map region from the sheet and generating its list numbers",
     "Fill in each map region and click Generate List Numbers/Next yourself",
     generate_each_region),
]

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def ask(prompt: str) -> str:
    """Reads one answer, or 'q' when the input ends (Ctrl-D, or run without a terminal)."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def run_steps(browser, folder: str, interactive: bool, rows: list, commit: bool = False) -> tuple:
    """Does each step, saving the screen after it. Returns (how each went, the notes)."""
    driver = browser.driver
    notes = {"name": rows[0]["map_region_name"], "rows": rows, "commit": commit}
    notes["save"] = lambda label: save_screen(driver, folder, "extra", label)
    steps = COMMIT_STEPS if commit else REHEARSAL_STEPS
    results = []

    for number, (name, doing, by_hand, step) in enumerate(steps, start=1):
        print(f"\n[{number}/{len(steps)}] {doing}...", flush=True)
        try:
            note = step(browser, notes)
            how = "automatic" + (f" ({note})" if note else "")
            print(f"    Done{': ' + note if note else '.'}", flush=True)
        except Exception as error:
            # An alert left open would make every later call fail, the save included.
            left_open = answer_alert(driver, accept=False)
            driver.switch_to.default_content()
            reason = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
            if left_open:
                reason += f" (VAN's alert said {left_open!r})"
            save_screen(driver, folder, number, f"{name}_stuck")
            print(f"    Could not do this by itself: {reason}\n"
                  f"    Saved the page it was stuck on.", flush=True)

            if not interactive:
                results.append((name, f"stuck: {reason}"))
                break

            answer = ask(f"    {by_hand} in Chrome, then press Enter here "
                         "(s to skip, q to stop): ")
            if answer == "q":
                results.append((name, f"stuck: {reason}"))
                break
            if answer == "s":
                results.append((name, f"skipped: {reason}"))
                continue
            how = f"by hand (stuck on: {reason})"
            if name == "my_turfs":
                notes["my_turfs_url"] = driver.current_url

        saved = save_screen(driver, folder, number, name)
        print(f"    Saved {len(saved)} files.", flush=True)
        results.append((name, how))

    notes.pop("save", None)
    return results, notes

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## LOGIN
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def quiet_driver(headless: bool = False):
    """
    Chrome as create_driver opens it, minus the log chatter, so the prompts can be read.
    The chatter is ChromeDriver's and Chrome's own logging, sent to the terminal by default.
    """
    options = webdriver.ChromeOptions()
    if os.environ.get("CHROME_BIN"):
        options.binary_location = os.environ["CHROME_BIN"]
    if headless:
        for argument in ["--headless", "--no-sandbox", "--window-size=1600,1200",
                         "--disable-dev-shm-usage", "--disable-gpu"]:
            options.add_argument(argument)
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    # Leave VAN's alerts open for the script to answer. By default ChromeDriver cancels any
    # alert it meets mid-command, and cancelling 'Generate list numbers?' cancels the generate.
    options.set_capability("unhandledPromptBehavior", "ignore")

    driver_path = os.environ.get("CHROMEDRIVER_PATH")
    service = (webdriver.ChromeService(executable_path=driver_path, log_output=subprocess.DEVNULL)
               if driver_path else webdriver.ChromeService(log_output=subprocess.DEVNULL))
    return webdriver.Chrome(options=options, service=service)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def pick_rows(region: str = None) -> list:
    """
    The Packet Tracker rows for one list: the one named, or the first on the sheet, plus
    every other row with the same name, since a search for the name shows them all.
    """
    rows = load_mapped_packets(region=[region] if region else None)
    name = rows[0]["map_region_name"]
    same = [row for row in rows if row["map_region_name"] == name]

    first = same[0]
    print(f"\nWorking on {name}: {len(same)} map region{'s' if len(same) != 1 else ''} on the "
          f"Packet Tracker ({', '.join(str(row['map_region_id']) for row in same)}) in "
          f"{first.get('folder_name') or first['folder_id']}.\n"
          f"    script {first.get('script')!r}, campaign {first.get('minivan_campaign') or 'none'!r}, "
          f"contacted how {CONTACTED_HOW!r}", flush=True)
    return same

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def capture_my_turfs(state: str, stored_login: bool, region: str = None,
                     commit: bool = False) -> str:
    """Logs in, puts the session where the print runs, and runs the steps. Returns the zip."""
    interactive = sys.stdin.isatty()
    rows = pick_rows(region)
    print("\nCOMMIT: list numbers WILL be generated." if commit
          else "\nREHEARSAL (no --commit): fills the page in, then clicks Exit. Nothing is "
               "generated.", flush=True)

    folder = os.path.join(ARTIFACT_DIR, f"my_turfs_capture_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(folder, exist_ok=True)

    driver = quiet_driver()
    browser = VanBrowser(driver)
    try:
        if stored_login:
            email, password, totp_secret = resolve_actionid_credentials(DEFAULT_ACTIONID_USER)
            print(f"Logging in as {email}", flush=True)
            browser.login(email, password, totp_secret)
        else:
            browser.wait_for_manual_login()

        try:
            browser.switch_session(state, committee_for(state), database=MY_VOTERS)
            print(f"Session is on {state}, My Voters.", flush=True)
        except Exception as error:
            print(f"Could not switch the session into {state} ({str(error).splitlines()[0]}).",
                  flush=True)
            if not interactive or ask("Pick the state and committee by hand in Chrome, then "
                                      "press Enter (q to stop): ") == "q":
                raise SystemExit("Stopped before My Turfs.")

        results, notes = run_steps(browser, folder, interactive, rows, commit)
    finally:
        driver.quit()

    write(os.path.join(folder, "steps.txt"),
          "\n".join(f"{name}: {how}" for name, how in results) + "\n")
    write(os.path.join(folder, "notes.json"), json.dumps(notes, indent=2, default=str))
    archive = shutil.make_archive(folder, "zip", folder)

    print("\n=== DONE ===")
    for name, how in results:
        print(f"  {name}: {how}")

    generated = notes.get("generated") or []
    if not commit:
        print("\nNOTHING WAS GENERATED: this was a rehearsal. Add --commit to generate.")
    elif generated:
        print(f"\nGENERATED list numbers for {notes['name']}: map region {', '.join(generated)}.")
    else:
        print(f"\nNOTHING WAS GENERATED for {notes['name']}: the run stopped before Generate "
              "(the step marked 'stuck' above says why).")
    print(f"\nSend this file back:\n    {os.path.abspath(archive)}")
    return archive

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Run the list-number flow on one list from the Packet Tracker and save each screen"
    )
    parser.add_argument("--stored-login", action="store_true",
                        help="Log in with the stored ActionID credentials and TOTP seed.")
    parser.add_argument("--region", metavar="NAME",
                        help="The list to work on, as the sheet names it. "
                             "Default: the first list on the Packet Tracker.")
    parser.add_argument("--commit", action="store_true",
                        help="Actually generate the list numbers. Without it, the page is "
                             "filled in and then exited.")
    parser.add_argument("--state", default="NY",
                        help="The state to switch the session into. Default NY.")
    args = parser.parse_args()

    capture_my_turfs(args.state, args.stored_login, args.region, args.commit)


if __name__ == "__main__":
    main()