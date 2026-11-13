"""
Reading and setting the dropdowns on a VAN page.

Split out of van_ui.py by size rather than by boundary. These are the same page
objects' internals, so they take the browser and use its driver and its settle,
which is what carries the run's timeout. VanBrowser keeps a method for each of
them, so callers and tests go on asking the browser.

VAN's dropdowns exist to run an onchange, so setting one fires its change event
by hand.
"""

from flows.van_automation.errors import VanUiError


def options(browser, selector: str) -> dict:
    """Returns {visible text: value} for a dropdown, blank options dropped."""
    found = browser.driver.execute_script(
        """
        const select = document.querySelector(arguments[0]);
        if (!select) { return null; }
        const options = {};
        for (const option of select.options) {
            const text = option.textContent.trim();
            if (text) { options[text] = option.value; }
        }
        return options;
        """,
        selector,
    )
    if found is None:
        raise VanUiError(f"No dropdown matching {selector!r}")
    return found


def selected_label(browser, selector: str) -> str:
    return browser.driver.execute_script(
        """
        const select = document.querySelector(arguments[0]);
        if (!select || select.selectedIndex < 0) { return ''; }
        return select.options[select.selectedIndex].textContent.trim();
        """,
        selector,
    )


def select_by_label(browser, selector: str, label: str):
    choices = options(browser, selector)
    if label not in choices:
        raise VanUiError(
            f"{label!r} is not an option in {selector!r}. It has: "
            f"{', '.join(sorted(choices))}"
        )
    set_select(browser, selector, value=choices[label])


def set_select(browser, selector: str, value: str):
    result = browser.driver.execute_script(
        """
        const select = document.querySelector(arguments[0]);
        if (!select) { return {missing: true}; }
        if (select.disabled) { return {disabled: true}; }
        select.value = arguments[1];
        if (select.value !== arguments[1]) { return {rejected: select.value}; }
        select.dispatchEvent(new Event('change', {bubbles: true}));
        return {value: select.value};
        """,
        selector,
        value,
    )
    if result.get("missing"):
        raise VanUiError(f"No dropdown matching {selector!r}")
    if result.get("disabled"):
        raise VanUiError(f"The dropdown {selector!r} is disabled")
    if "rejected" in result:
        raise VanUiError(
            f"{selector!r} would not take {value!r}, it holds {result['rejected']!r}"
        )
    browser._settle()




# Finds the checkbox whose label reads exactly arguments[0]: a <label> tied to it, or a
# bare text element sitting next to it. Leaves the matches in `boxes`.
_FIND_CHECKBOX = """
const text = arguments[0];
const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
const boxes = [];
const add = (box) => { if (box && box.type === 'checkbox' && !boxes.includes(box)) { boxes.push(box); } };
for (const label of document.querySelectorAll('label')) {
    if (norm(label.textContent) !== text) { continue; }
    let box = label.control || (label.htmlFor && document.getElementById(label.htmlFor));
    if (!box) {
        const prev = label.previousElementSibling;
        if (prev && prev.matches('input[type=checkbox]')) { box = prev; }
    }
    add(box);
}
if (!boxes.length) {
    for (const el of document.querySelectorAll('span, td, div')) {
        if (el.children.length || norm(el.textContent) !== text) { continue; }
        const prev = el.previousElementSibling;
        if (prev && prev.matches('input[type=checkbox]')) { add(prev); continue; }
        const near = el.parentElement ? el.parentElement.querySelectorAll('input[type=checkbox]') : [];
        if (near.length === 1) { add(near[0]); }
    }
}
"""


def checkbox_checked(browser, label: str) -> bool:
    """Whether the checkbox labelled `label` is ticked."""
    found = browser.driver.execute_script(
        _FIND_CHECKBOX + "return {count: boxes.length, checked: boxes.length ? boxes[0].checked : null};",
        label,
    )
    if found["count"] != 1:
        raise VanUiError(f"Found {found['count']} checkboxes labelled {label!r}, expected one")
    return found["checked"]


def set_checkbox(browser, label: str, checked: bool) -> bool:
    """
    Ticks or unticks the checkbox labelled `label` and returns its state once the page
    settles. It is clicked rather than set, so VAN's own handlers run as they would for
    a person.
    """
    result = browser.driver.execute_script(
        _FIND_CHECKBOX + """
        if (boxes.length !== 1) { return {count: boxes.length}; }
        const box = boxes[0];
        if (box.disabled) { return {count: 1, disabled: true, checked: box.checked}; }
        if (box.checked !== arguments[1]) { box.click(); }
        return {count: 1, checked: box.checked};
        """,
        label,
        checked,
    )
    if result["count"] != 1:
        raise VanUiError(f"Found {result['count']} checkboxes labelled {label!r}, expected one")
    if result.get("disabled") and result["checked"] != checked:
        raise VanUiError(f"The {label!r} checkbox is disabled and {'unticked' if checked else 'ticked'}")

    browser._settle()
    state = checkbox_checked(browser, label)
    if state != checked:
        raise VanUiError(f"The {label!r} checkbox would not {'tick' if checked else 'untick'}")
    return state