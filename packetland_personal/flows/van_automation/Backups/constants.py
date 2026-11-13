"""
Locators for the VoteBuilder screens these flows drive, and the few numbers that
go with them.

ASP.NET control id fragments, mostly. Label text is deliberately not used to find
controls: VAN keeps hidden panels in the DOM, so a label lookup can match
something that is not on screen.

Nothing is imported here on purpose, so this stays a file of names with no
behaviour behind them.
"""

# Every page these flows visit.
VOTEBUILDER = "https://www.votebuilder.com"

# Where a screenshot and the page source go when a district fails. These runs are
# long and unattended, and VAN state is not reproducible after the fact.
ARTIFACT_DIR = "van_automation_artifacts"

### -------------------------------------------------------------------------- ###
## FOLDER DETAIL
### -------------------------------------------------------------------------- ###

# Reached by obfuscated id. This page is the one with the Print Region link.
FOLDER_DETAIL_URL = f"{VOTEBUILDER}/FolderDetailList.aspx"

# The grid pages at 40 rows, so it is filtered before it is read.
FOLDER_TYPE_FILTER = "select[id$='_VanInputItemviiFilterType']"
FOLDER_NAME_FILTER = "input[id$='_VanInputItemviiFilterName']"
FOLDER_FILTER_BUTTON = "input[id$='_RefreshFilterButton']"
MAP_REGION_TYPE = "Map Region"

FOLDER_ROWS = "table[id$='_gvList'] tbody tr"
FOLDER_ROW_NAME = "a[data-popover-type='Map Region'] span.grid-result"
# Every row renders the anchor; only a map region's carries an href. The label sits in
# data-original-title: Bootstrap moves it out of title when it wires the tooltip.
FOLDER_ROW_PRINT = "a[data-original-title='Print Region'][href]"
FOLDER_ROW_ID_CELL = 1
FOLDER_ROW_REFRESHED_CELL = 9

# Print Region's href is a LoadLists() call that confirms and then posts this form.
RUN_SEARCH_FORM = "RunSearch"
LOAD_LISTS_URL_PATTERN = r"LoadLists\('([^']+)'\)"

### -------------------------------------------------------------------------- ###
## PRINT REGION WIZARD
### -------------------------------------------------------------------------- ###

PRINT_WIZARD_URL_FRAGMENT = "ScriptSortOptions.aspx"

# Matched on visible text, which is what the sheet holds. The ids end in
# ...InputItemDetailsItemX_X with no underscore before the first X.
PRINT_MINIVAN_CAMPAIGN_SELECT = "select[id$='ItemMiniVANCampaign_MiniVANCampaign']"
PRINT_REPORT_FORMAT_SELECT = "select[id$='ItemReportFormatInfo_ReportFormatInfo']"
PRINT_SCRIPT_SELECT = "select[id$='ItemActiveScriptID_ActiveScriptID']"
PRINT_CONTACTED_HOW_SELECT = "select[id$='ItemScriptSource_ScriptSource']"

PRINT_TITLE_INPUT = "input[id$='ItemReportTitle_ReportTitle']"
PRINT_NEXT_BUTTON = "input[id$='_ButtonSortOptionsSubmit']"
PRINT_SORT_FEEDBACK = "span[id$='_LabelSortOrderFeedback']"

# Door packets. Picking Walk also applies VAN's walk sort defaults.
CONTACTED_HOW = "Walk"

# See fill_print_wizard for why this order.
PRINT_FIELD_ORDER = ["report_format", "script", "contacted_how", "minivan_campaign"]

# Checkboxes on the print wizard, found by the label next to them. They are set after
# the dropdowns, which can reset them. True ticks the box, False unticks it.
PRINT_CHECKBOXES = {
    "Print People": False,
}

# Where Next lands when VAN accepts the job.
PRINT_JOB_WAIT_MARKER = "#vanContent.van-PDFPrintJobWait"
PRINT_JOB_WAIT_TEXT = "Your PDF is being created."

### -------------------------------------------------------------------------- ###
## PDF PRINT JOBS
### -------------------------------------------------------------------------- ###

# Every PDF this login printed in the state the session is on, newest first, forty
# to a page. A finished job's row carries a download link; nothing else does.
PDF_JOB_LIST_URL = f"{VOTEBUILDER}/PDFPrintJobList.aspx"
PDF_DOWNLOAD_URL = f"{VOTEBUILDER}/FileExportHandler.ashx"

# Direct children only: the pager in the tfoot is a table of its own.
PDF_JOB_ROWS = "table#gv > tbody > tr"
PDF_JOB_ID = "span[data-jobid]"
PDF_JOB_TITLE_CELL = 1
PDF_JOB_TYPE_CELL = 2
PDF_JOB_CREATED_CELL = 3
PDF_JOB_DOWNLOAD_LINK = "td.downloadLink a"
PDF_JOB_PAGER = "table#gv > tfoot"
PDF_JOB_PAGES_PATTERN = r"(\d+)\s+Pages?"

# Paging is an ASP.NET postback on the grid.
PDF_JOB_GRID_EVENT_TARGET = "ctl00$ContentPlaceHolderVANPage$gv"

# What the Type column says for a print off a map region.
PDF_JOB_MAP_REGION_TYPE = "Map Region"

# Created, as the grid renders it: 9/26/26 12:41 AM, in the state's own time.
PDF_JOB_CREATED_FORMAT = "%m/%d/%y %I:%M %p"