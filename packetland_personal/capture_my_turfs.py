"""
Saves the My Turfs screens, so the list-number flow can be written against the real page.

Opens Chrome, logs in, switches into the state, then waits while you click through My
Turfs by hand. Each time you press Enter it saves what is on screen: a screenshot, the
URL, the page and every frame inside it, and every form control with its current value
(page source alone does not show which boxes are ticked or what a dropdown is set to).

It never clicks anything in VAN itself, so nothing happens there that you do not do.
At the end it zips everything into one file to send back.

    python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

from selenium.webdriver.common.by import By

from core_utilities.van_browser_utilities import MY_VOTERS
from flows.van_automation.session import open_session
from flows.van_automation.target_slots import committee_for
from flows.van_automation.van_ui import ARTIFACT_DIR

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## CAPTURE MY TURFS ------------------------------------------------------------------------------------------------------------------------------ ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# Same reason as USE_STRICT in van_ui: some VAN pages walk the call stack and throw on
# ChromeDriver's wrapper unless the script reading them is strict.
STRICT = "'use strict';\n"

# The screens to save, in the order you reach them. Each is (file name, what to put on screen).
STEPS = [
    ("my_turfs", "Open My Turfs, with no filters applied."),
    ("filters", "Click 'Filters | Edit' so every filter option is showing. Open any dropdown "
                "in it that looks like it picks a folder or a turf name."),
    ("ticked", "Close the filters and tick ONE map region's box (a bold parent row). Pick a "
               "region you were going to generate a list number for anyway."),
    ("quick_actions", "Open the Quick Actions dropdown."),
    ("modal", "Choose Generate List Number, so the 'Generate List Numbers for the Selected "
              "Turfs' box is up."),
    ("generate_page", "Click Next, so the Generate List Numbers page shows Map Region 1."),
    ("generate_filled", "Fill in MiniVAN Campaign, Script, Contacted How and Title the way "
                        "you normally would, but do not click anything yet."),
    ("after_generate", "Now click 'Generate List Numbers/Next'. Save whatever page comes up."),
    ("list_number", "Back on My Turfs, find that region so its new list number shows in "
                    "the row (expand it if the number is on the turfs underneath)."),
]

# How deep to follow frames inside frames. VAN's popups are one frame down; two is spare.
FRAME_DEPTH = 2

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

def save_screen(driver, folder: str, index: int, name: str) -> list:
    """
    Saves everything on screen as NN_name.*, in every open tab. Returns the files written.

    Every tab, because a VAN action can open its page in a new one, and the frames in each,
    because VAN's popups are pages of their own inside the one you see.
    """
    saved = []
    start = driver.current_window_handle
    handles = driver.window_handles

    for tab, handle in enumerate(handles, start=1):
        base = os.path.join(folder, f"{index:02d}_{name}")
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
            write(base + ".error.txt", str(error))

    driver.switch_to.window(start)
    driver.switch_to.default_content()
    return saved

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def ask(prompt: str) -> str:
    """Reads one answer, or 'q' when the input ends (Ctrl-D, or run without a terminal)."""
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def walk_steps(driver, folder: str) -> int:
    """Waits on each step in turn, then any extras, and returns how many screens were saved."""
    count = 0

    print("\nClick through VAN in the Chrome window. At each step, get the page on screen, "
          "then come back here and press Enter.\nType s to skip a step, or q to stop.\n",
          flush=True)

    for name, instruction in STEPS:
        print(f"\n[{count + 1}] {instruction}", flush=True)
        answer = ask("    Enter when it is on screen (s to skip, q to stop): ").lower()
        if answer == "q":
            return count
        if answer == "s":
            continue
        count += 1
        saved = save_screen(driver, folder, count, name)
        print(f"    Saved {len(saved)} files.", flush=True)

    while True:
        extra = ask("\nAnything else worth saving? Get it on screen, type a short name "
                    "and press Enter, or just press Enter to finish: ")
        if not extra or extra.lower() == "q":
            return count
        count += 1
        name = re.sub(r"[^A-Za-z0-9]+", "_", extra).strip("_") or "extra"
        saved = save_screen(driver, folder, count, name)
        print(f"    Saved {len(saved)} files.", flush=True)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def capture_my_turfs(state: str, stored_login: bool) -> str:
    """Logs in, puts the session where the print runs, and saves the screens. Returns the zip."""
    if not sys.stdin.isatty():
        raise SystemExit("This waits for you at each step, so it has to be run in a terminal.")

    folder = os.path.join(ARTIFACT_DIR, f"my_turfs_capture_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(folder, exist_ok=True)

    driver, browser = open_session(headless=False, stored_login=stored_login)
    try:
        try:
            browser.switch_session(state, committee_for(state), database=MY_VOTERS)
            print(f"Session is on {state}, My Voters.", flush=True)
        except Exception as error:
            print(f"Could not switch the session into {state} ({str(error).splitlines()[0]}).\n"
                  "Pick the state and committee by hand in Chrome, then carry on.", flush=True)

        count = walk_steps(driver, folder)
    finally:
        driver.quit()

    archive = shutil.make_archive(folder, "zip", folder)
    print(f"\nSaved {count} screens. Send this file back:\n    {os.path.abspath(archive)}")
    return archive

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Save the My Turfs screens for writing the list-number flow"
    )
    parser.add_argument("--stored-login", action="store_true",
                        help="Log in with the stored ActionID credentials and TOTP seed.")
    parser.add_argument("--state", default="NY",
                        help="The state to switch the session into. Default NY.")
    args = parser.parse_args()

    capture_my_turfs(args.state, args.stored_login)


if __name__ == "__main__":
    main()
