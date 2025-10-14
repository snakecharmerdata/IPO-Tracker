## IPO Tracker Application

This document provides a comprehensive overview of the `ipo3.py` script, a standalone IPO (Initial Public Offering) tracking application built with Python and `tkinter`.

### 1. Core Purpose

The primary goal of this application is to provide users with a simple, desktop-based tool to monitor upcoming IPOs. It fetches data from **IPO Scoop**, displays it in a sortable and filterable table, and allows users to schedule local notifications for IPOs they are interested in.

A key feature is the built-in **research dialog**, which allows users to instantly access more information about a company, including official websites, news articles, and other relevant links, all from within the application.

### 2. Key Features

#### a. Data Fetching and Parsing

- **Primary Data Source**: The script fetches IPO data from the IPO Scoop calendar (`https://www.iposcoop.com/ipo-calendar/`).
- **Robust Parsing**: It uses the `BeautifulSoup` library to parse the HTML table if it is installed, which is the most reliable method. 
- **Fallback Mechanism**: If `BeautifulSoup` is not available, it falls back to a regex-based parser to extract data from the table. This ensures the application can still function even without all optional dependencies installed.

#### b. Graphical User Interface (GUI)

- **Framework**: The entire UI is built using `tkinter`, Python's standard GUI library.
- **Main Window**: 
  - Displays IPO data in a `ttk.Treeview` widget, which provides a sortable, multicolumn table.
  - Includes controls for refreshing data, filtering by category, and searching across all fields.
  - Provides buttons for managing notifications, saving/loading choices, and accessing instructions.
- **Status Bar**: A status bar at the top right keeps the user informed about the application's current state (e.g., "Fetching data...", "Ready").

#### c. Company Research and Analysis

- **Analyze Button**: Each row in the IPO table has a magnifying glass icon (🔍). Clicking it opens the `CompanyAnalysisDialog`.
- **Dynamic Link Generation**: This dialog dynamically gathers information about the selected company from various web sources:
  - **Official Websites**: It tries to find the company's official website by parsing its Wikipedia page and running targeted web searches.
  - **Articles & Buzz**: It scrapes search results from DuckDuckGo, Reuters, and MarketWatch to find recent news and articles related to the company's IPO.
  - **Research Links**: It provides direct links to search for the company on Yahoo Finance, Reuters, and the SEC's EDGAR database.
- **Clickable Hyperlinks**: All URLs displayed in the dialog are **active hyperlinks**. The text is colored blue and underlined, and the cursor changes to a hand icon on hover. Clicking a link opens it in the user's default web browser.
- **In-App Preview (Optional)**: If the `tkinterweb` library is installed, a checkbox appears to "Open links in app." If checked, URLs will be opened in a basic HTML frame within the dialog itself.

#### d. Notification System

- **Local Notifications**: Users can schedule local notifications for specific IPOs by clicking the bell icon (🔔) in a row or by double-clicking the row to configure options.
- **Timing**: Notifications can be set for 24 hours and/or 3 hours before the expected trade date.
- **Cross-Platform Support**: The script uses native notification systems where available:
  - **macOS**: `terminal-notifier` (if installed) or AppleScript.
  - **Windows**: `win10toast` (if installed) or a PowerShell fallback.
  - **Linux**: `notify-send`.
- **Persistence**: Scheduled notifications are saved to `ipo_notifications.json` in the user's application data directory, so they persist between application restarts.

#### e. Data Persistence

- **Saving Choices**: Users can save their notification settings (which IPOs are enabled, timing preferences) to a file named `ipo_choices.json`.
- **Loading Choices**: These saved settings can be loaded back into the application, which automatically re-enables notifications for the saved IPOs.

### 3. Technical Implementation

- **Threading**: Network operations (fetching IPO data, company research) are performed in background threads to keep the GUI responsive.
- **Dependency Handling**: The script is designed to be as self-contained as possible.
  - It checks for the presence of `BeautifulSoup` and `tkinterweb` at runtime.
  - If `tkinterweb` is missing, it attempts to install it automatically using `pip`.
  - If key libraries are unavailable, it gracefully degrades functionality (e.g., uses regex parsing, disables the in-app preview) instead of crashing.
- **CompanyAnalysisDialog**: This class is now fully implemented within `ipo3.py` to avoid external file dependencies. Its key mechanism for providing clickable links is:
  - The `tkinter.Text` widget is **not disabled**. Instead, it is made read-only by binding `<Key>` and `<<Paste>>` events to a function that does nothing (`lambda e: "break"`). This prevents user edits but allows the widget to process mouse events.
  - Each URL is inserted into the text widget, and a unique `tag` is applied to the exact character range of the URL. This tag is configured with a blue, underlined font and bindings for `<Button-1>` (click), `<Enter>` (hover), and `<Leave>` (un-hover) to handle the click action and cursor changes.

### 4. How to Run the Script

1.  **Save the Code**: Save the entire script as `ipo3.py`.
2.  **Install Dependencies**: While the script attempts to handle some dependencies automatically, it is best to pre-install them. Open a terminal and run:
    ```sh
    pip install requests beautifulsoup4 tkinterweb
    ```
3.  **Execute the Script**: Run the application from your terminal:
    ```sh
    python ipo3.py
    ```
4.  **Usage**:
    - Click the **Refresh** button to load the latest IPO data.
    - Click the magnifying glass (🔍) on any row to open the research dialog and access clickable links.
    - Click the bell (🔔) to schedule a notification for an IPO.
