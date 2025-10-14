#!/usr/bin/env python3
"""
Clean IPO Tracker script without nltk, with robust year-aware date parsing.
- Fetches IPO calendar from IPO Scoop (BeautifulSoup optional)
- Displays data in Tkinter table
- Simple categorization by keywords (no NLP libs)
- Local notification scheduling (24h and 3h before if in the future)
"""

import re
import html
import json
import queue
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import pickle
import os
import os.path
import subprocess
import platform
import time
import shutil
import webbrowser

import requests

# This is the new implementation that ensures clickable links.
class CompanyAnalysisDialog:
    def __init__(self, parent, ipo: "IPO"):
        self.parent = parent
        self.ipo = ipo
        self.win = None

    def show(self):
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:
            return

        if self.win and bool(self.win.winfo_exists()):
            self.win.lift()
            self.win.focus_force()
            return

        self.win = tk.Toplevel(self.parent)
        self.win.title(f"Research: {self.ipo.company} ({self.ipo.symbol})")
        self.win.geometry("750x600")
        self.win.transient(self.parent)
        self.win.grab_set()

        container = ttk.Frame(self.win, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text=f"{self.ipo.company} ({self.ipo.symbol})", font=("TkDefaultFont", 14, "bold")).pack(anchor=tk.W)
        ttk.Separator(container, orient="horizontal").pack(fill=tk.X, pady=(6, 10))

        toolbar = ttk.Frame(container)
        toolbar.pack(fill=tk.X, pady=(0, 6))
        if ensure_webview_available():
            self._open_in_app_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(toolbar, text="Open links in app (preview)", variable=self._open_in_app_var).pack(side=tk.LEFT)

        self.txt = tk.Text(container, wrap="word", relief=tk.FLAT, font=("TkDefaultFont", 10))
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=vsb.set)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.web_container = ttk.Frame(container)
        self.web_container.pack_forget()

        self.txt.insert("1.0", "Loading research...\n")
        self.txt.config(cursor="arrow", state="disabled")

        import threading
        threading.Thread(target=self._gather_and_render, daemon=True).start()

    def _add_clickable_line(self, text: str, url: str):
        tag = f"link_{abs(hash(url)) & 0xFFFFFF:x}"
        start_index = self.txt.index("end-1c")
        self.txt.insert("end", text + "\n")
        end_index = self.txt.index("end-2c")
        self.txt.tag_add(tag, start_index, end_index)
        self.txt.tag_configure(tag, foreground="#1976d2", underline=True)

        def _open(evt=None, u=url):
            print(f"Attempting to open URL: {u}")
            self._open_url(u)

        self.txt.tag_bind(tag, "<Button-1>", _open)
        self.txt.tag_bind(tag, "<Enter>", lambda e: self.txt.config(cursor="hand2"))
        self.txt.tag_bind(tag, "<Leave>", lambda e: self.txt.config(cursor="arrow"))

    def _ensure_webview(self):
        if not ensure_webview_available() or not HtmlFrame:
            return None
        if getattr(self, "webview", None) and self.webview.winfo_exists():
            return self.webview
        self.txt.pack_forget()
        self.web_container.pack(fill="both", expand=True, pady=(8, 0))
        self.webview = HtmlFrame(self.web_container, messages_enabled=False)
        self.webview.pack(fill="both", expand=True)
        return self.webview

    def _open_url(self, url: str):
        use_preview = getattr(self, "_open_in_app_var", None) and self._open_in_app_var.get()
        if use_preview and ensure_webview_available():
            vw = self._ensure_webview()
            if vw:
                vw.load_website(url)
                return
        import webbrowser
        webbrowser.open(url)

    def _ddg_search(self, query: str, max_results: int = 5):
        import urllib.parse
        results = []
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            html = http_get(url, timeout=8).decode("utf-8", errors="ignore")
            if HAS_BS4:
                soup = BeautifulSoup(html, "html.parser")
                entries = [(a.get("href", ""), a.get_text(" ", strip=True)) for a in soup.find_all("a", class_="result__a", href=True)]
            else:
                entries = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', html, flags=re.I)
            count = 0
            for href, title_html in entries:
                try:
                    from urllib.parse import urlparse, parse_qs, unquote
                    if href.startswith('/l/'):
                        href = unquote(parse_qs(urlparse(href).query)['uddg'][0])
                except Exception:
                    pass
                title = strip_tags(title_html) if "<" in title_html else (title_html or "")
                if href and title:
                    results.append((title[:120], href))
                    count += 1
                if count >= max_results:
                    break
        except Exception:
            pass
        return results

    def _get_wikipedia_url(self, company: str) -> str:
        try:
            import urllib.parse
            api_url = f"https://en.wikipedia.org/w/api.php?action=query&format=json&prop=info&inprop=url&redirects=1&titles={urllib.parse.quote(company)}&formatversion=2"
            data = json.loads(http_get(api_url, timeout=6).decode("utf-8", errors="ignore"))
            pages = data.get("query", {}).get("pages", [])
            if pages and not pages[0].get("missing"):
                return pages[0].get("fullurl") or ""
        except Exception:
            pass
        return ""

    def _collect_links(self, company: str):
        import urllib.parse
        from urllib.parse import urlparse
        websites, links, articles, seen_links, seen_articles = [], [], [], set(), set()

        def _hostname(url: str) -> str:
            try:
                h = urlparse(url).netloc.lower().split(":")[0]
                return h[4:] if h.startswith("www.") else h
            except Exception:
                return ""

        def add_link(title, url):
            if url and url not in seen_links:
                seen_links.add(url)
                links.append({"title": title, "url": url})

        def add_article(title, url):
            if url and url not in seen_articles:
                seen_articles.add(url)
                articles.append({"title": title, "url": url})

        def add_site(title, url):
            host = _hostname(url)
            if host and not any(_hostname(s[1]) == host for s in websites):
                websites.append((title, url))

        wiki_url = self._get_wikipedia_url(company)
        if wiki_url:
            try:
                html = http_get(wiki_url, timeout=6).decode("utf-8", errors="ignore")
                if HAS_BS4:
                    soup = BeautifulSoup(html, "html.parser")
                    infobox = soup.find("table", class_=re.compile(r"infobox", re.I))
                    if infobox:
                        website_row = infobox.find("th", string=re.compile(r"Website", re.I))
                        if website_row:
                            a = website_row.find_next("td").find("a", href=True)
                            if a: add_site("Official Website (Wikipedia)", a["href"])
            except Exception:
                pass

        toks = [t for t in re.sub(r"[^a-z0-9\s]", " ", company.lower()).split() if t not in {"inc", "corp", "co", "company", "ltd", "group"}]
        for q in (f"{company} official site", f"{company} website"):
            for title, url in self._ddg_search(q, max_results=3):
                host = _hostname(url)
                if host and not any(host.endswith(d) for d in ["wikipedia.org", "reuters.com", "yahoo.com"]):
                    if any(t in host for t in toks):
                        add_site("Official Website (Search)", url)
                        break
            if websites: break

        ddg_ipo = self._ddg_search(f"{company} IPO", max_results=8)
        for title, url in ddg_ipo:
            add_article(title, url)

        if wiki_url: add_link("Wikipedia", wiki_url)
        add_link("Yahoo Finance", f"https://finance.yahoo.com/lookup?s={urllib.parse.quote(company)}")
        add_link("Reuters", f"https://www.reuters.com/site-search/?query={urllib.parse.quote(company)}")
        add_link("SEC EDGAR", f"https://www.sec.gov/edgar/search/#/q={urllib.parse.quote(company)}")

        return websites, links, articles, len(ddg_ipo)

    def _gather_and_render(self):
        try:
            websites, links, articles, buzz_count = self._collect_links(self.ipo.company)
            summary = get_quick_info(self.ipo.company)
        except Exception:
            summary, websites, links, articles, buzz_count = "", [], [], [], 0

        def render():
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")

            self.txt.insert("end", "Websites\n", ("h1",))
            if websites:
                for i, (title, url) in enumerate(websites, 1):
                    self.txt.insert("end", f"{i}. {title}: ")
                    self._add_clickable_line(url, url)
            else:
                self.txt.insert("end", "No official website found.\n")
            self.txt.insert("end", "\n")

            self.txt.insert("end", "Articles & Buzz\n", ("h1",))
            if articles:
                for i, item in enumerate(articles, 1):
                    self.txt.insert("end", f"{i}. ")
                    self._add_clickable_line(item['title'], item['url'])
            else:
                self.txt.insert("end", "No articles found.\n")
            self.txt.insert("end", "\n")

            self.txt.insert("end", "Research Links\n", ("h1",))
            if links:
                for i, item in enumerate(links, 1):
                    self.txt.insert("end", f"{i}. {item['title']}: ")
                    self._add_clickable_line(item['url'], item['url'])
            self.txt.insert("end", "\n")

            self.txt.insert("end", "Summary\n", ("h1",))
            self.txt.insert("end", (summary or "No summary available.") + "\n")

            self.txt.tag_configure("h1", font=("TkDefaultFont", 11, "bold"))
            self.txt.config(state="disabled")

        self.parent.after(0, render)

# Optional dependencies
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except Exception:
    HAS_BS4 = False

# UI
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception:
    print("This application requires tkinter.")
    raise

# Optional in-app web preview
try:
    from tkinterweb import HtmlFrame  # type: ignore
    HAS_WEBVIEW = True
except Exception:
    HAS_WEBVIEW = False
    HtmlFrame = None  # type: ignore

def ensure_webview_available() -> bool:
    """Attempt to ensure tkinterweb is available. Returns True if usable."""
    global HAS_WEBVIEW, HtmlFrame
    if HAS_WEBVIEW:
        return True
    try:
        import sys
        import subprocess as _sub
        _sub.run([sys.executable, "-m", "pip", "install", "--quiet", "tkinterweb"], check=False)
        from tkinterweb import HtmlFrame as _HtmlFrame  # type: ignore
        HtmlFrame = _HtmlFrame  # type: ignore
        HAS_WEBVIEW = True
        return True
    except Exception:
        return False

# Google Calendar integration disabled for executable build
GOOGLE_CALENDAR_AVAILABLE = False

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

CATEGORIES = [
    "Financial", "Pharmaceutical", "AI", "Engineering", "Defense", "Robotics",
    "Services", "Communications", "Materials", "Quantum", "Transportation",
    "Raw Products", "Consumer Goods", "Industrials", "Consumer Products", 
    "Healthcare", "Information Technology", "Real Estate", "Energy", "Utilities",
    "Distributor", "Reseller"
]


def get_app_data_dir() -> str:
    try:
        system = platform.system()
        if system == "Darwin":
            base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "IPO Tracker")
        elif system == "Windows":
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "IPO Tracker")
        else:
            base = os.path.join(os.path.expanduser("~"), ".config", "ipo-tracker")
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return os.path.expanduser("~")


def get_storage_path(name: str) -> str:
    return os.path.join(get_app_data_dir(), name)


def show_system_notification(title: str, message: str, sound: bool = True):
    """Native notification (macOS, Windows fallback, Linux notify-send)."""
    system = platform.system()
    try:
        if system == "Darwin":
            # Prefer terminal-notifier if available (often more reliable/visible), else fallback to AppleScript
            try:
                if shutil.which("terminal-notifier"):
                    cmd = [
                        "terminal-notifier",
                        "-title", title,
                        "-message", message,
                    ]
                    subprocess.run(cmd, check=False)
                else:
                    script = f'display notification "{message}" with title "{title}"'
                    if sound:
                        script += ' sound name "Glass"'
                    subprocess.run(["osascript", "-e", script], check=False)
            except Exception:
                # Fallback to AppleScript if terminal-notifier path fails unexpectedly
                script = f'display notification "{message}" with title "{title}"'
                if sound:
                    script += ' sound name "Glass"'
                subprocess.run(["osascript", "-e", script], check=False)
        elif system == "Windows":
            try:
                import win10toast  # type: ignore
                toaster = win10toast.ToastNotifier()
                toaster.show_toast(title, message, duration=10, threaded=True)
            except Exception:
                subprocess.run([
                    "powershell", "-Command",
                    f'Add-Type -AssemblyName System.Windows.Forms; '
                    f'[System.Windows.Forms.MessageBox]::Show("{message}", "{title}")'
                ], check=False)
        else:
            subprocess.run(["notify-send", title, message], check=False)
    except Exception as e:
        print(f"Failed to show notification: {e}")


@dataclass
class IPO:
    company: str
    symbol: str
    lead_managers: str
    shares_m: str
    price_low: str
    price_high: str
    est_volume: str
    exp_trade: str
    scoop_rating: str
    rating_change: str
    category: str = field(default="?", compare=False)
    _info_snippet: Optional[str] = field(default=None, repr=False, compare=False)
    notify_enabled: bool = field(default=False, compare=False)
    notify_24h: bool = field(default=True, compare=False)
    notify_3h: bool = field(default=True, compare=False)

    def get_sort_key(self, column: str):
        if column == "exp_trade":
            dt = self.get_trade_datetime()
            return dt if dt else datetime.max
        if column in ("shares_m", "est_volume", "price_low", "price_high"):
            try:
                val = re.sub(r"[^0-9.]", "", getattr(self, column)) or "0"
                return float(val)
            except Exception:
                return 0.0
        if column == "category":
            return self.category
        return getattr(self, column).lower()

    def get_trade_datetime(self) -> Optional[datetime]:
        """
        Parse expected trade date. Supports:
        - MM/DD/YYYY
        - MM/DD/YY
        - MONTH DD, YYYY  (e.g., SEPT 11, 2025)
        - MONTH DD        (infers current or next year)
        - YYYY-MM-DD
        - DD              (infers current or next month)
        """
        try:
            text = (self.exp_trade or "").strip().upper()
            if not text:
                return None

            now = datetime.now()

            # ISO YYYY-MM-DD
            m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
            if m:
                y, mo, d = map(int, m.groups())
                return datetime(y, mo, d, 9, 30)

            # MM/DD/YYYY
            m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
            if m:
                mo, d, y = map(int, m.groups())
                return datetime(y, mo, d, 9, 30)

            # MM/DD/YY
            m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", text)
            if m:
                mo, d, yy = map(int, m.groups())
                y = 2000 + yy if yy < 70 else 1900 + yy
                return datetime(y, mo, d, 9, 30)

            # Month names
            month_map = {
                'JAN': 1, 'JANUARY': 1,
                'FEB': 2, 'FEBRUARY': 2,
                'MAR': 3, 'MARCH': 3,
                'APR': 4, 'APRIL': 4,
                'MAY': 5,
                'JUN': 6, 'JUNE': 6,
                'JUL': 7, 'JULY': 7,
                'AUG': 8, 'AUGUST': 8,
                'SEP': 9, 'SEPT': 9, 'SEPTEMBER': 9,
                'OCT': 10, 'OCTOBER': 10,
                'NOV': 11, 'NOVEMBER': 11,
                'DEC': 12, 'DECEMBER': 12
            }

            # MONTH DD, YYYY (comma optional)
            m = re.fullmatch(r"([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
            if m:
                mon_s, d_s, y_s = m.groups()
                mon = month_map.get(mon_s)
                if mon:
                    return datetime(int(y_s), mon, int(d_s), 9, 30)

            # MONTH DD (infer year: current or next if already passed)
            m = re.fullmatch(r"([A-Z]+)\s+(\d{1,2})", text)
            if m:
                mon_s, d_s = m.groups()
                mon = month_map.get(mon_s)
                if mon:
                    d = int(d_s)
                    dt = datetime(now.year, mon, d, 9, 30)
                    if dt < now:
                        dt = datetime(now.year + 1, mon, d, 9, 30)
                    return dt

            # Day only (assume current month or next if past)
            m = re.fullmatch(r"(\d{1,2})", text)
            if m:
                d = int(m.group(1))
                y, mo = now.year, now.month
                try:
                    dt = datetime(y, mo, d, 9, 30)
                    if dt < now:
                        if mo == 12:
                            dt = datetime(y + 1, 1, d, 9, 30)
                        else:
                            dt = datetime(y, mo + 1, d, 9, 30)
                    return dt
                except ValueError:
                    return None

            # Fallbacks: search for recognizable date substrings anywhere in the field
            try:
                raw = (self.exp_trade or "").strip()
                # 1) MM/DD/YYYY anywhere
                m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", raw)
                if m:
                    try:
                        dt = datetime.strptime(m.group(1), "%m/%d/%Y").replace(hour=9, minute=30)
                        if dt < now:
                            # Already past — treat as no date
                            return None
                        return dt
                    except Exception:
                        pass
                # 2) YYYY-MM-DD anywhere
                m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
                if m:
                    y, mo, d = map(int, m.groups())
                    try:
                        dt = datetime(y, mo, d, 9, 30)
                        if dt < now:
                            return None
                        return dt
                    except Exception:
                        pass
                # 3) WEEK OF MONTH DD[, YYYY]
                m = re.search(r"WEEK OF\s+([A-Z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", text)
                if m:
                    mon_s, d_s, y_s = m.groups()
                    mon_map = {
                        'JAN': 1, 'JANUARY': 1, 'FEB': 2, 'FEBRUARY': 2, 'MAR': 3, 'MARCH': 3,
                        'APR': 4, 'APRIL': 4, 'MAY': 5, 'JUN': 6, 'JUNE': 6, 'JUL': 7, 'JULY': 7,
                        'AUG': 8, 'AUGUST': 8, 'SEP': 9, 'SEPT': 9, 'SEPTEMBER': 9, 'OCT': 10, 'OCTOBER': 10,
                        'NOV': 11, 'NOVEMBER': 11, 'DEC': 12, 'DECEMBER': 12
                    }
                    mon = mon_map.get(mon_s)
                    if mon:
                        y = int(y_s) if y_s else now.year
                        d = int(d_s)
                        try:
                            dt = datetime(y, mon, d, 9, 30)
                            if dt < now and not y_s:
                                # If year missing and computed date is past, roll to next year
                                dt = datetime(now.year + 1, mon, d, 9, 30)
                            if dt > now:
                                return dt
                        except Exception:
                            pass
            except Exception:
                pass

            return None
        except Exception:
            return None


def http_get(url: str, timeout: int = 16) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_tags(html_text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_ipos_from_iposcoop() -> Tuple[List[IPO], Optional[str]]:
    url = "https://www.iposcoop.com/ipo-calendar/"
    try:
        content = http_get(url)
        html_text = content.decode("utf-8", errors="ignore")
        ipos: List[IPO] = []
        if not HAS_BS4:
            return [], "BeautifulSoup4 is required for IPO Scoop parsing."
        soup = BeautifulSoup(html_text, "html.parser")
        table = soup.find("table", class_="standard-table ipolist")
        if not table:
            return [], "No IPO calendar found on IPO Scoop."
        headers = [strip_tags(str(th)).lower() for th in table.find_all("th")]
        header_map = {
            "company": "company",
            "symbol proposed": "symbol",
            "lead managers": "lead_managers",
            "shares (millions)": "shares_m",
            "price low": "price_low",
            "price high": "price_high",
            "est. $ volume": "est_volume",
            "expected to trade": "exp_trade",
            "scoop rating": "scoop_rating",
            "rating change": "rating_change",
        }
        for row in table.find_all("tr")[1:]:
            cells = [strip_tags(str(td)) for td in row.find_all(["td", "th"])]
            if len(cells) != len(headers):
                continue
            mapped = {header_map.get(headers[i], headers[i]): cells[i].strip() for i in range(len(headers))}
            ipo = IPO(
                company=mapped.get("company", ""),
                symbol=mapped.get("symbol", ""),
                lead_managers=mapped.get("lead_managers", ""),
                shares_m=mapped.get("shares_m", ""),
                price_low=mapped.get("price_low", ""),
                price_high=mapped.get("price_high", ""),
                est_volume=mapped.get("est_volume", ""),
                exp_trade=mapped.get("exp_trade", ""),
                scoop_rating=mapped.get("scoop_rating", ""),
                rating_change=mapped.get("rating_change", ""),
                category="?",
            )
            if not ipo.company or not ipo.symbol:
                continue
            if ipo.company.lower() in ("company", "total", "week of"):
                continue
            ipos.append(ipo)
        return ipos, None
    except urllib.error.URLError as e:
        return [], f"Network error fetching IPO Scoop: {e}"
    except Exception as e:
        return [], f"Error parsing IPO Scoop: {e}"


# Fallback parser that does not require BeautifulSoup
# Attempts to parse the IPO Scoop table using regex/string operations.
def parse_iposcoop_table_without_bs4(html_text: str) -> List[IPO]:
    try:
        # Locate the IPO list table by class name containing 'ipolist'
        m = re.search(r"<table[^>]*class=\"[^\"]*ipolist[^\"]*\"[^>]*>([\s\S]*?)</table>", html_text, re.I)
        if not m:
            # Broader attempt: any table with 'standard-table' and 'ipo' fragments
            m = re.search(r"<table[^>]*class=\"[^\"]*(standard-table[^\"]*ipo|ipo[^\"]*standard-table)[^\"]*\"[^>]*>([\s\S]*?)</table>", html_text, re.I)
        if not m:
            return []
        table_html = m.group(0)

        # Extract rows
        rows = re.findall(r"<tr[\s\S]*?</tr>", table_html, re.I)
        if len(rows) < 2:
            return []

        # Header row parsing
        header_cells_html = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", rows[0], re.I)
        headers = [strip_tags(c).lower() for c in header_cells_html]
        header_map = {
            "company": "company",
            "symbol proposed": "symbol",
            "lead managers": "lead_managers",
            "shares (millions)": "shares_m",
            "price low": "price_low",
            "price high": "price_high",
            "est. $ volume": "est_volume",
            "expected to trade": "exp_trade",
            "scoop rating": "scoop_rating",
            "rating change": "rating_change",
        }

        ipos: List[IPO] = []
        for row in rows[1:]:
            cell_htmls = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row, re.I)
            cells = [strip_tags(c) for c in cell_htmls]
            if len(cells) != len(headers):
                continue
            mapped = {header_map.get(headers[i], headers[i]): (cells[i].strip() if i < len(cells) else "") for i in range(len(headers))}
            company = mapped.get("company", "").strip()
            symbol = mapped.get("symbol", "").strip()
            if not company or not symbol:
                continue
            if company.lower() in ("company", "total", "week of"):
                continue
            ipo = IPO(
                company=company,
                symbol=symbol,
                lead_managers=mapped.get("lead_managers", ""),
                shares_m=mapped.get("shares_m", ""),
                price_low=mapped.get("price_low", ""),
                price_high=mapped.get("price_high", ""),
                est_volume=mapped.get("est_volume", ""),
                exp_trade=mapped.get("exp_trade", ""),
                scoop_rating=mapped.get("scoop_rating", ""),
                rating_change=mapped.get("rating_change", ""),
                category="?",
            )
            ipos.append(ipo)
        return ipos
    except Exception:
        return []


def fetch_ipos_from_iposcoop2() -> Tuple[List[IPO], Optional[str]]:
    url = "https://www.iposcoop.com/ipo-calendar/"
    try:
        content = http_get(url)
        html_text = content.decode("utf-8", errors="ignore")

        # Primary path: BeautifulSoup if available and table is found
        if HAS_BS4:
            try:
                soup = BeautifulSoup(html_text, "html.parser")
                table = soup.find("table", class_="standard-table ipolist")
                if table:
                    headers = [strip_tags(str(th)).lower() for th in table.find_all("th")]
                    header_map = {
                        "company": "company",
                        "symbol proposed": "symbol",
                        "lead managers": "lead_managers",
                        "shares (millions)": "shares_m",
                        "price low": "price_low",
                        "price high": "price_high",
                        "est. $ volume": "est_volume",
                        "expected to trade": "exp_trade",
                        "scoop rating": "scoop_rating",
                        "rating change": "rating_change",
                    }
                    ipos: List[IPO] = []
                    for row in table.find_all("tr")[1:]:
                        cells = [strip_tags(str(td)) for td in row.find_all(["td", "th"])]
                        if len(cells) != len(headers):
                            continue
                        mapped = {header_map.get(headers[i], headers[i]): cells[i].strip() for i in range(len(headers))}
                        company = mapped.get("company", "").strip()
                        symbol = mapped.get("symbol", "").strip()
                        if not company or not symbol:
                            continue
                        if company.lower() in ("company", "total", "week of"):
                            continue
                        ipo = IPO(
                            company=company,
                            symbol=symbol,
                            lead_managers=mapped.get("lead_managers", ""),
                            shares_m=mapped.get("shares_m", ""),
                            price_low=mapped.get("price_low", ""),
                            price_high=mapped.get("price_high", ""),
                            est_volume=mapped.get("est_volume", ""),
                            exp_trade=mapped.get("exp_trade", ""),
                            scoop_rating=mapped.get("scoop_rating", ""),
                            rating_change=mapped.get("rating_change", ""),
                            category="?",
                        )
                        ipos.append(ipo)
                    if ipos:
                        return ipos, None
            except Exception:
                pass

        # Fallback path: regex-based parsing without BeautifulSoup
        ipos_fb = parse_iposcoop_table_without_bs4(html_text)
        if ipos_fb:
            return ipos_fb, None
        return [], "No IPO calendar found on IPO Scoop."
    except urllib.error.URLError as e:
        return [], f"Network error fetching IPO Scoop: {e}"
    except Exception as e:
        return [], f"Error parsing IPO Scoop: {e}"


class NotificationScheduler:
    def __init__(self):
        self.scheduled_notifications: Dict[str, Dict] = {}
        self.scheduler_thread: Optional[threading.Thread] = None
        self.running: bool = False
        self.notifications_file = get_storage_path("ipo_notifications.json")
        self.on_notify = None
        self.load_notifications()

    def load_notifications(self):
        try:
            if os.path.exists(self.notifications_file):
                with open(self.notifications_file, "r") as f:
                    data = json.load(f)
                for key, notification in data.items():
                    notification["notify_times"] = [datetime.fromisoformat(dt) for dt in notification["notify_times"]]
                    # Restore trade_datetime if saved as ISO string
                    td = notification.get("trade_datetime")
                    if isinstance(td, str):
                        try:
                            notification["trade_datetime"] = datetime.fromisoformat(td)
                        except Exception:
                            notification["trade_datetime"] = None
                self.scheduled_notifications = data
                # Auto-start scheduler if any future notifications exist (handles app restart case)
                try:
                    now = datetime.now()
                    has_future = any(
                        any(t > now for t in n.get("notify_times", []))
                        for n in self.scheduled_notifications.values()
                    )
                    if has_future and not self.running:
                        self.start_scheduler()
                except Exception:
                    pass
        except Exception as e:
            print(f"Failed to load notifications: {e}")
            self.scheduled_notifications = {}

    def save_notifications(self):
        try:
            data = {}
            for key, notification in self.scheduled_notifications.items():
                data[key] = notification.copy()
                data[key]["notify_times"] = [dt.isoformat() for dt in notification["notify_times"]]
                # Ensure trade_datetime is serialized
                td = notification.get("trade_datetime")
                if isinstance(td, datetime):
                    data[key]["trade_datetime"] = td.isoformat()
            with open(self.notifications_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to save notifications: {e}")

    def set_notify_callback(self, callback):
        self.on_notify = callback

    def schedule_ipo_notification(self, ipo: IPO) -> bool:
        dt = ipo.get_trade_datetime()
        if not dt:
            print(f"Could not parse trade datetime for {ipo.company}: '{ipo.exp_trade}'")
            return False
        now = datetime.now()
        # Ensure the provided Month/Date/Year is in the future relative to now
        if dt <= now:
            print(f"Trade datetime is not in the future for {ipo.company}: trade={dt}, now={now}")
            return False
        notify_times = []
        # Fixed notification times at 8:00 AM local
        base_8am = datetime(dt.year, dt.month, dt.day, 8, 0)
        if ipo.notify_24h:
            day_before_8 = base_8am - timedelta(days=1)
            if day_before_8 > now:
                notify_times.append(day_before_8)
        if ipo.notify_3h:
            # Same day at 8:00 AM only if IPO date is strictly in the future
            if dt.date() <= now.date():
                # Do not schedule same-day for current or past date
                pass
            else:
                if base_8am > now:
                    notify_times.append(base_8am)
        if not notify_times:
            print(f"No future notification times for {ipo.company} (trade: {dt}, now: {now})")
            return False
        key = f"{ipo.company}_{ipo.symbol}"
        self.scheduled_notifications[key] = {
            "company": ipo.company,
            "symbol": ipo.symbol,
            "trade_date": ipo.exp_trade,
            "category": ipo.category,
            "price_range": f"${ipo.price_low} - ${ipo.price_high}",
            "notify_times": notify_times,
            "trade_datetime": dt,
        }
        self.save_notifications()
        if not self.running:
            self.start_scheduler()
        return True

    def remove_ipo_notification(self, ipo: IPO):
        key = f"{ipo.company}_{ipo.symbol}"
        if key in self.scheduled_notifications:
            del self.scheduled_notifications[key]
            self.save_notifications()

    def start_scheduler(self):
        if not self.running:
            self.running = True
            self.scheduler_thread = threading.Thread(target=self._loop, daemon=True)
            self.scheduler_thread.start()

    def stop_scheduler(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                now = datetime.now()
                to_remove = []
                for key, notif in list(self.scheduled_notifications.items()):
                    due = []
                    for t in list(notif["notify_times"]):
                        if now >= t:
                            self._send_notification(notif)
                            due.append(t)
                    for t in due:
                        notif["notify_times"].remove(t)
                    if not notif["notify_times"]:
                        to_remove.append(key)
                for k in to_remove:
                    del self.scheduled_notifications[k]
                if to_remove:
                    self.save_notifications()
                time.sleep(60)
            except Exception as e:
                print(f"Scheduler error: {e}")
                time.sleep(60)

    def _send_notification(self, notification: Dict):
        dt = notification["trade_datetime"]
        delta = dt - datetime.now()
        minutes = int(max(delta.total_seconds(), 0) // 60)
        msg_time = f"{minutes // 60} hours" if minutes >= 60 else f"{minutes} minutes"
        title = f"IPO Alert: {notification['company']}"
        message = (
            f"{notification['symbol']} begins trading in {msg_time}\n"
            f"Category: {notification['category']}\n"
            f"Price Range: {notification['price_range']}\n"
            f"Trade Date: {notification['trade_date']}"
        )
        payload = {"title": title, "message": message, "notification": notification}
        if callable(getattr(self, "on_notify", None)):
            try:
                self.on_notify(payload)
            except Exception as e:
                print(f"Notify callback failed, falling back to system notification: {e}")
                show_system_notification(title, message, sound=True)
        else:
            show_system_notification(title, message, sound=True)

    def get_scheduled_count(self) -> int:
        return sum(len(n['notify_times']) for n in self.scheduled_notifications.values())


class GoogleCalendarManager:
    SCOPES = ['https://www.googleapis.com/auth/calendar']

    def __init__(self):
        self.service = None
        self.credentials = None

    def authenticate(self) -> bool:
        if not GOOGLE_CALENDAR_AVAILABLE:
            return False
        try:
            token_path = get_storage_path('token.pickle')
            if os.path.exists(token_path):
                with open(token_path, 'rb') as token:
                    self.credentials = pickle.load(token)
            if not self.credentials or not getattr(self.credentials, 'valid', False):
                if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                    self.credentials.refresh(Request())
                else:
                    cred_path_cwd = 'credentials.json'
                    cred_path_data = get_storage_path('credentials.json')
                    cred_path = cred_path_cwd if os.path.exists(cred_path_cwd) else (cred_path_data if os.path.exists(cred_path_data) else None)
                    if not cred_path:
                        return False
                    flow = InstalledAppFlow.from_client_secrets_file(cred_path, self.SCOPES)
                    self.credentials = flow.run_local_server(port=0)
                with open(token_path, 'wb') as token:
                    pickle.dump(self.credentials, token)
            self.service = build('calendar', 'v3', credentials=self.credentials)
            return True
        except Exception as e:
            print(f"Google Calendar authentication failed: {e}")
            return False

    def create_ipo_event(self, ipo: IPO) -> bool:
        if not self.service:
            return False
        try:
            dt = ipo.get_trade_datetime()
            if not dt:
                return False
            event = {
                'summary': f'IPO: {ipo.company} ({ipo.symbol}) Trading Begins',
                'description': (
                    f"IPO Trading Information:\n"
                    f"Company: {ipo.company}\n"
                    f"Symbol: {ipo.symbol}\n"
                    f"Category: {ipo.category}\n"
                    f"Price Range: ${ipo.price_low} - ${ipo.price_high}\n"
                    f"Shares: {ipo.shares_m}M\n"
                    f"Est. Volume: {ipo.est_volume}\n"
                    f"Lead Managers: {ipo.lead_managers}\n"
                    f"SCOOP Rating: {ipo.scoop_rating}\n\n"
                    f"Expected to begin trading at market open."
                ),
                'start': {'dateTime': dt.isoformat(), 'timeZone': 'America/New_York'},
                'end': {'dateTime': (dt + timedelta(hours=1)).isoformat(), 'timeZone': 'America/New_York'},
                'reminders': {'useDefault': False, 'overrides': []},
            }
            if ipo.notify_24h:
                event['reminders']['overrides'].append({'method': 'popup', 'minutes': 24 * 60})
            if ipo.notify_3h:
                event['reminders']['overrides'].append({'method': 'popup', 'minutes': 3 * 60})
            _ = self.service.events().insert(calendarId='primary', body=event).execute()
            return True
        except Exception as e:
            print(f"Failed to create calendar event: {e}")
            return False


class NotificationDialog:
    def __init__(self, parent, ipo: IPO):
        self.parent = parent
        self.ipo = ipo
        self.result = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"Notifications for {ipo.company}")
        self.dialog.geometry("400x300")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
        self._build_dialog()

    def _build_dialog(self):
        main = ttk.Frame(self.dialog, padding="20")
        main.pack(fill=tk.BOTH, expand=True)

        info = ttk.LabelFrame(main, text="IPO Information", padding="10")
        info.pack(fill=tk.X, pady=(0, 20))
        ttk.Label(info, text=f"Company: {self.ipo.company}").pack(anchor=tk.W)
        ttk.Label(info, text=f"Symbol: {self.ipo.symbol}").pack(anchor=tk.W)
        ttk.Label(info, text=f"Expected Trade Date: {self.ipo.exp_trade}").pack(anchor=tk.W)
        ttk.Label(info, text=f"Category: {self.ipo.category}").pack(anchor=tk.W)

        opts = ttk.LabelFrame(main, text="Notification Settings", padding="10")
        opts.pack(fill=tk.X, pady=(0, 20))
        self.notify_24h_var = tk.BooleanVar(value=self.ipo.notify_24h)
        self.notify_3h_var = tk.BooleanVar(value=self.ipo.notify_3h)
        ttk.Checkbutton(opts, text="Notify 1 day before at 8:00 AM", variable=self.notify_24h_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(opts, text="Notify same day at 8:00 AM", variable=self.notify_3h_var).pack(anchor=tk.W, pady=2)

        buttons = ttk.Frame(main)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side=tk.RIGHT)

    def _save(self):
        self.result = {
            'notify_24h': self.notify_24h_var.get(),
            'notify_3h': self.notify_3h_var.get()
        }
        self.dialog.destroy()

    def _cancel(self):
        self.result = None
        self.dialog.destroy()

    def show(self):
        self.dialog.wait_window()
        return self.result


class IPOTrackerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("IPO Tracker - IPO Scoop")
        self.geometry("1400x730")

        self._data: List[IPO] = []
        self._filtered: List[IPO] = []
        self._fetch_thread: Optional[threading.Thread] = None
        self._fetch_queue: "queue.Queue[Tuple[List[IPO], Optional[str], str]]" = queue.Queue()
        self._cat_thread: Optional[threading.Thread] = None
        self._cat_queue: "queue.Queue[None]" = queue.Queue()
        self._notification_scheduler = NotificationScheduler()
        self._notification_scheduler.set_notify_callback(self._handle_notification)
        # Notification sound preference (default ON)
        self.sound_enabled = tk.BooleanVar(value=True)
        # Badge/base title
        self._base_title = "IPO Tracker - IPO Scoop"
        self.unread_alerts = 0
        self._update_badge()

        self._build_ui()
        self._bind_events()
        self.after(100, self.refresh_data)
        self.after(5000, self._update_notification_status)

    def _update_notification_status(self):
        count = self._notification_scheduler.get_scheduled_count()
        if count > 0:
            self.status_var.set(f"Ready | {count} notifications scheduled")
        else:
            self.status_var.set("Ready")
        self.after(5000, self._update_notification_status)

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)

        # Brand visual: buildings icon + IPO Tracker
        self.brand = tk.Label(top, text="🏙 IPO Tracker", font=("TkDefaultFont", 24, "bold"), fg="#1976d2")
        self.brand.pack(side=tk.LEFT, padx=(0, 16))

        self.cat_var = tk.StringVar(value="All")
        ttk.Label(top, text="Category:").pack(side=tk.LEFT)
        self.cat_cb = ttk.Combobox(top, textvariable=self.cat_var, values=["All"] + CATEGORIES + ["Unknown"], state="readonly", width=18)
        self.cat_cb.pack(side=tk.LEFT, padx=(4, 12))

        self.search_var = tk.StringVar()
        ttk.Label(top, text="Search:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=40)
        self.search_entry.pack(side=tk.LEFT, padx=(4, 12))

        self.refresh_btn = ttk.Button(top, text="Refresh", command=self.refresh_data)
        self.refresh_btn.pack(side=tk.LEFT)

        ttk.Separator(top, orient='vertical').pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(top, text="Setup Notifications", command=self._setup_notifications).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Save Choices", command=self._save_current_choices).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Load Choices", command=self._load_choices).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Remove Selected", command=self._remove_selected_notifications).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Remove All", command=self._remove_all_notifications).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Instructions", command=self._show_instructions).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(top, text="Test Notify Now", command=self._test_notification_now).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Checkbutton(top, text="Sound", variable=self.sound_enabled).pack(side=tk.LEFT, padx=(0, 5))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status_var, foreground="#666").pack(side=tk.RIGHT)

        columns = ("notify", "analyze", "company", "symbol", "lead_managers", "shares_m", "price_low", "price_high", "est_volume", "exp_trade", "scoop_rating", "rating_change", "category")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        headers = {
            "notify": "🔔", "analyze": "Analyze", "company": "Company", "symbol": "Symbol Proposed", "lead_managers": "Lead Managers",
            "shares_m": "Shares (M)", "price_low": "Price Low", "price_high": "Price High",
            "est_volume": "Est. $ Volume", "exp_trade": "Expected to Trade",
            "scoop_rating": "SCOOP Rating", "rating_change": "Rating Change", "category": "Category"
        }
        widths = {
            "notify": 40, "analyze": 60, "company": 200, "symbol": 80, "lead_managers": 200, "shares_m": 90, "price_low": 80, "price_high": 80,
            "est_volume": 110, "exp_trade": 140, "scoop_rating": 100, "rating_change": 100, "category": 120
        }
        self._sort_state: Dict[str, bool] = {k: False for k in headers}
        for key in columns:
            heading_kwargs = {"text": headers[key]}
            if key not in ("notify", "analyze"):
                heading_kwargs["command"] = (lambda c=key: self.sort_by(c))
            self.tree.heading(key, **heading_kwargs)
            self.tree.column(key, width=widths[key], anchor=tk.W if key != "notify" else tk.CENTER)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.place(in_=self.tree, relx=1.0, x=0, y=0, relheight=1.0, anchor='ne')

        # Bind click on notify column
        self.tree.bind("<Button-1>", self._on_single_click)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Bottom panel: scheduled notifications (ticker in blue, date in orange)
        self.bottom_frame = ttk.Frame(self)
        self.bottom_frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))
        ttk.Separator(self.bottom_frame, orient='horizontal').pack(fill=tk.X, pady=(4, 4))
        self.selection_label = ttk.Label(self.bottom_frame, text="Scheduled Notifications", font=("TkDefaultFont", 10, "bold"))
        self.selection_label.pack(anchor=tk.W, pady=(0, 4))
        self.selection_text = tk.Text(self.bottom_frame, height=8, wrap="none", relief=tk.FLAT)
        self.selection_text.tag_configure("ticker", foreground="#1976d2", font=("TkDefaultFont", 10, "bold"))
        self.selection_text.tag_configure("date", foreground="#f57c00", font=("TkDefaultFont", 10))
        self.selection_text.tag_configure("dim", foreground="#888888")
        self.selection_text.configure(state="disabled")
        self.selection_text.pack(fill=tk.X)

        # Initial and periodic refresh of the bottom panel
        self._refresh_selected_panel()
        self.after(2000, self._refresh_selected_panel_periodic)

    def _bind_events(self) -> None:
        self.search_var.trace_add("write", lambda *_: self.apply_filters())
        self.cat_cb.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

    def _on_single_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            if column == "#1":  # notify column
                item = self.tree.identify_row(event.y)
                if item:
                    self._toggle_notification(item)
            elif column == "#2":  # analyze column
                item = self.tree.identify_row(event.y)
                if item:
                    self._analyze_company(item)

    def _on_double_click(self, event):
        item = self.tree.selection()[0] if self.tree.selection() else None
        if item:
            self._configure_notification(item)

    def _toggle_notification(self, item):
        idx = self.tree.index(item)
        if idx < len(self._filtered):
            ipo = self._filtered[idx]
            ipo.notify_enabled = not ipo.notify_enabled
            if ipo.notify_enabled:
                if self._notification_scheduler.schedule_ipo_notification(ipo):
                    timing = []
                    if ipo.notify_24h:
                        timing.append("1 day 8AM")
                    if ipo.notify_3h:
                        timing.append("same day 8AM")
                    timing_str = " and ".join(timing) if timing else ""
                    messagebox.showinfo(
                        "Notification Scheduled",
                        f"Notifications scheduled for {ipo.company}.\n"
                        f"Timing: {timing_str}."
                    )
                else:
                    messagebox.showwarning(
                        "No Future Notifications",
                        f"No future notifications can be scheduled for {ipo.company}.\n"
                        f"The trading date '{ipo.exp_trade}' may be too soon or already passed."
                    )
                    ipo.notify_enabled = False
            else:
                self._notification_scheduler.remove_ipo_notification(ipo)
                messagebox.showinfo("Notifications Removed", f"Notifications removed for {ipo.company}.")
            self._update_tree_item(item, ipo)

    def _analyze_company(self, item):
        """Open the analysis dialog for the selected company."""
        idx = self.tree.index(item)
        if idx < len(self._filtered):
            ipo = self._filtered[idx]
            dialog = CompanyAnalysisDialog(self, ipo)
            dialog.show()

    def _configure_notification(self, item):
        idx = self.tree.index(item)
        if idx < len(self._filtered):
            ipo = self._filtered[idx]
            dialog = NotificationDialog(self, ipo)
            result = dialog.show()
            if result:
                ipo.notify_enabled = True
                ipo.notify_24h = result['notify_24h']
                ipo.notify_3h = result['notify_3h']
                dt = ipo.get_trade_datetime()
                if dt and ipo.notify_3h and dt.date() <= datetime.now().date():
                    messagebox.showwarning(
                        "Cannot Schedule Same-Day",
                        "This IPO is current or in the past cannot be saved. IPO save scheduale is for at least 1 day prior."
                    )
                    ipo.notify_3h = False
                if self._notification_scheduler.schedule_ipo_notification(ipo):
                    timing = []
                    if ipo.notify_24h:
                        timing.append("1 day 8AM")
                    if ipo.notify_3h:
                        timing.append("same day 8AM")
                    timing_str = " and ".join(timing) if timing else ""
                    msg = f"Notifications scheduled for {ipo.company}!"
                    if timing_str:
                        msg += f"\nTiming: {timing_str}."
                    messagebox.showinfo("Success", msg)
                else:
                    ipo.notify_enabled = False
                    messagebox.showwarning("No Future Notifications", "The trading date may be too soon or already passed.")
                self._update_tree_item(item, ipo)

    def _setup_notifications(self):
        items = self.tree.selection()
        if not items:
            messagebox.showinfo("No Selection", "Please select one or more IPOs to setup notifications.")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Bulk Notification Setup")
        dialog.geometry("350x200")
        dialog.transient(self)
        dialog.grab_set()

        main = ttk.Frame(dialog, padding="20")
        main.pack(fill=tk.BOTH, expand=True)
        ttk.Label(main, text=f"Setup notifications for {len(items)} selected IPOs:").pack(anchor=tk.W, pady=(0, 10))

        notify_24h_var = tk.BooleanVar(value=True)
        notify_3h_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(main, text="Notify 1 day before at 8:00 AM", variable=notify_24h_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(main, text="Notify same day at 8:00 AM", variable=notify_3h_var).pack(anchor=tk.W, pady=2)

        buttons = ttk.Frame(main)
        buttons.pack(fill=tk.X, pady=(20, 0))

        def apply_settings():
            success = 0
            for item in items:
                idx = self.tree.index(item)
                if idx < len(self._filtered):
                    ipo = self._filtered[idx]
                    ipo.notify_enabled = True
                    ipo.notify_24h = notify_24h_var.get()
                    ipo.notify_3h = notify_3h_var.get()
                    if self._notification_scheduler.schedule_ipo_notification(ipo):
                        success += 1
                    else:
                        ipo.notify_enabled = False
                    self._update_tree_item(item, ipo)
            dialog.destroy()
            if success > 0:
                messagebox.showinfo("Success", f"Notifications setup for {success} IPOs!")
            else:
                messagebox.showwarning("No Notifications", "No future notifications could be scheduled.")

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(buttons, text="Apply", command=apply_settings).pack(side=tk.RIGHT)

    def _update_tree_item(self, item, ipo: IPO):
        notify_symbol = "✓" if ipo.notify_enabled else ""
        values = (
            notify_symbol,
            "🔍",  # Analyze icon
            ipo.company,
            ipo.symbol,
            ipo.lead_managers,
            ipo.shares_m,
            ipo.price_low,
            ipo.price_high,
            ipo.est_volume,
            ipo.exp_trade,
            ipo.scoop_rating,
            ipo.rating_change,
            ipo.category,
        )
        self.tree.item(item, values=values)

    def _save_current_choices(self):
        """Save all current IPO reminder choices and related fields to ipo_choices.json."""
        try:
            payload = []
            for ipo in self._data:
                # Only save IPOs that have notifications enabled
                if not ipo.notify_enabled:
                    continue
                item = {
                    "company": ipo.company,
                    "symbol": ipo.symbol,
                    "exp_trade": ipo.exp_trade,
                    "parsed_trade_datetime": None,
                    "notify_enabled": ipo.notify_enabled,
                    "notify_24h": ipo.notify_24h,
                    "notify_3h": ipo.notify_3h,
                    "category": ipo.category,
                    "price_low": ipo.price_low,
                    "price_high": ipo.price_high,
                    "shares_m": ipo.shares_m,
                    "est_volume": ipo.est_volume,
                    "lead_managers": ipo.lead_managers,
                    "scoop_rating": ipo.scoop_rating,
                    "rating_change": ipo.rating_change,
                }
                dt = ipo.get_trade_datetime()
                if dt:
                    item["parsed_trade_datetime"] = dt.isoformat()
                payload.append(item)

            data = {
                "saved_at": datetime.now().isoformat(),
                "count": len(payload),
                "choices": payload,
            }
            choices_path = get_storage_path("ipo_choices.json")
            with open(choices_path, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Saved", f"Saved {len(payload)} IPO choices to:\n{choices_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save choices: {e}")

    def _load_choices(self):
        """Load previously saved choices from ipo_choices.json and apply them."""
        try:
            choices_path = get_storage_path("ipo_choices.json")
            with open(choices_path, "r") as f:
                data = json.load(f)
            choices = data.get("choices", [])
            if not choices:
                messagebox.showinfo("Load Choices", "No choices found in ipo_choices.json")
                return

            mapped_by_symbol: Dict[str, IPO] = {ipo.symbol.upper(): ipo for ipo in self._data if ipo.symbol}
            mapped_by_company: Dict[str, IPO] = {ipo.company.lower(): ipo for ipo in self._data if ipo.company}

            applied = 0
            scheduled = 0
            for ch in choices:
                sym = (ch.get("symbol") or "").upper()
                comp = (ch.get("company") or "").lower()
                ipo = None
                if sym and sym in mapped_by_symbol:
                    ipo = mapped_by_symbol[sym]
                elif comp and comp in mapped_by_company:
                    ipo = mapped_by_company[comp]
                if not ipo:
                    continue

                # Apply flags
                ipo.notify_enabled = bool(ch.get("notify_enabled", False))
                ipo.notify_24h = bool(ch.get("notify_24h", True))
                ipo.notify_3h = bool(ch.get("notify_3h", True))
                applied += 1

                # Re-schedule if enabled
                if ipo.notify_enabled:
                    if self._notification_scheduler.schedule_ipo_notification(ipo):
                        scheduled += 1
                    else:
                        # If scheduling failed (past times), keep the enabled flag but notify user later
                        pass

            # Refresh the table view to reflect ✓ marks
            self.apply_filters()
            messagebox.showinfo("Load Choices", f"Applied {applied} choice(s). Scheduled {scheduled} reminder set(s).")
        except FileNotFoundError:
            messagebox.showwarning("Load Choices", f"Choices file not found at:\n{get_storage_path('ipo_choices.json')}\nSave choices first.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load choices: {e}")

    def _refresh_selected_panel(self):
        try:
            # Build list of scheduled notifications from actual notify_times
            items = []
            notifs = self._notification_scheduler.scheduled_notifications
            for key, n in notifs.items():
                symbol = n.get("symbol") or ""
                notify_times = n.get("notify_times") or []
                for t in notify_times:
                    try:
                        when_str = t.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        continue
                    items.append((symbol, when_str))
            # Sort by date/time string (ISO-like) for stable display
            items.sort(key=lambda x: x[1])

            self.selection_text.configure(state="normal")
            self.selection_text.delete("1.0", tk.END)
            if not items:
                self.selection_text.insert(tk.END, "No notifications scheduled.\n", ("dim",))
            else:
                for symbol, when_str in items:
                    self.selection_text.insert(tk.END, symbol, ("ticker",))
                    self.selection_text.insert(tk.END, "  ")
                    self.selection_text.insert(tk.END, when_str + "\n", ("date",))
            self.selection_text.configure(state="disabled")
        except Exception:
            try:
                self.selection_text.configure(state="disabled")
            except Exception:
                pass

    def _refresh_selected_panel_periodic(self):
        self._refresh_selected_panel()
        self.after(2000, self._refresh_selected_panel_periodic)

    def _show_instructions(self):
        win = tk.Toplevel(self)
        win.title("Instructions")
        win.geometry("700x600")
        win.transient(self)
        win.grab_set()

        container = ttk.Frame(win, padding="12")
        container.pack(fill=tk.BOTH, expand=True)

        txt = tk.Text(container, wrap="word")
        vsb = ttk.Scrollbar(container, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        instructions = (
            "IPO Tracker - Instructions\n\n"
            "Overview:\n"
            "- This app fetches upcoming IPOs from IPO Scoop and displays them in a sortable table.\n"
            "- You can search, filter by category, and schedule notifications.\n\n"
            "Header Bar:\n"
            "- 🏙 IPO Tracker: App title.\n"
            "- Category: filter by detected category.\n"
            "- Search: full-text filter across all columns.\n"
            "- Refresh: pull the latest IPO calendar.\n"
            "- Setup Notifications: bulk configure for selected rows.\n"
            "- Save Choices: save currently enabled notifications into ipo_choices.json.\n"
            "- Load Choices: restore saved selections and re-schedule future reminders.\n"
            "- Remove Selected / Remove All: clear notifications.\n"
            "- Instructions: open this guide.\n\n"
            "Sorting:\n"
            "- Click any column header (except the bell) to sort ascending/descending.\n\n"
            "Notifications:\n"
            "- Click the bell column (first column) to toggle notifications for a row.\n"
            "- Double-click a row to open a dialog and set: 1 day before at 8:00 AM and/or same day at 8:00 AM.\n"
            "- Same-day at 8:00 AM is blocked if the IPO is today or in the past.\n"
            "  The app will show: 'This IPO is current or in the past cannot be saved. IPO save scheduale is for at least 1 day prior.'\n"
            "- The bottom panel lists the scheduled items: ticker (blue) and date/time (orange).\n"
            "- Notifications are processed while the app is running.\n\n"
            "Saving/Loading:\n"
            "- Save Choices stores ONLY IPOs with notifications enabled.\n"
            "- Load Choices matches saved rows by symbol/company and re-schedules future notifications.\n\n"
            "Notes:\n"
            "- Date parsing supports multiple formats and infers year/day when needed.\n"
            "- All local times; reminders persist in ipo_notifications.json.\n"
        )
        txt.insert("1.0", instructions)
        txt.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)

    def _test_notification_now(self):
        payload = {
            "title": "IPO Tracker — Test",
            "message": "This is a test notification from IPO Tracker.",
            "notification": {
                "company": "Test Corp",
                "symbol": "TEST",
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "category": "Test",
                "price_range": "$—",
            },
        }
        self._show_in_app_notification(payload)

    def _handle_notification(self, payload: Dict):
        # Ensure UI work is done on the Tk main thread
        self.after(0, lambda: self._show_in_app_notification(payload))

    def _show_in_app_notification(self, payload: Dict):
        title = payload.get("title", "IPO Alert")
        message = payload.get("message", "")
        notif = payload.get("notification", {})
        company = notif.get("company", "")
        symbol = notif.get("symbol", "")
        trade_date = notif.get("trade_date", "")
        category = notif.get("category", "")
        price_range = notif.get("price_range", "")

        # Ensure the app is visible even if minimized
        try:
            self.deiconify()
        except Exception:
            pass

        top = tk.Toplevel(self)
        top.title(title)
        top.transient(self)
        top.grab_set()
        top.attributes("-topmost", True)
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass
        # Flash taskbar on Windows to draw attention
        try:
            self._flash_taskbar(True)
        except Exception:
            pass

        # Size and center on screen
        w, h = 460, 260
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        top.geometry(f"{w}x{h}+{x}+{y}")

        frm = ttk.Frame(top, padding=20)
        frm.pack(fill=tk.BOTH, expand=True)
        # Increment unread badge count
        try:
            self.unread_alerts += 1
            self._update_badge()
        except Exception:
            pass

        ttk.Label(frm, text=title, font=("TkDefaultFont", 12, "bold")).pack(anchor=tk.W)
        ttk.Separator(frm, orient="horizontal").pack(fill=tk.X, pady=8)
        if company or symbol:
            ttk.Label(frm, text=f"{company} ({symbol})", font=("TkDefaultFont", 11)).pack(anchor=tk.W)
        if trade_date:
            ttk.Label(frm, text=f"Trade Date: {trade_date}").pack(anchor=tk.W, pady=(2,0))
        if category:
            ttk.Label(frm, text=f"Category: {category}").pack(anchor=tk.W)
        if price_range:
            ttk.Label(frm, text=f"Price Range: {price_range}").pack(anchor=tk.W)
        if message:
            ttk.Label(frm, text="\n" + message, wraplength=w-40, justify=tk.LEFT).pack(anchor=tk.W)

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(16,0))

        def _close_popup():
            try:
                top.destroy()
            finally:
                # Stop flashing taskbar (Windows)
                try:
                    self._flash_taskbar(False)
                except Exception:
                    pass
                try:
                    if self.unread_alerts > 0:
                        self.unread_alerts -= 1
                        self._update_badge()
                except Exception:
                    pass

        ttk.Button(btns, text="OK", command=_close_popup).pack(side=tk.RIGHT)
        try:
            top.protocol("WM_DELETE_WINDOW", _close_popup)
        except Exception:
            pass

        # Play sound if enabled
        if self.sound_enabled.get():
            self._play_alert_sound()

    def _set_app_badge(self, count: int):
        try:
            if platform.system() == "Darwin":
                try:
                    from AppKit import NSApplication  # type: ignore
                    app = NSApplication.sharedApplication()
                    tile = app.dockTile()
                    tile.setBadgeLabel_(str(count) if count > 0 else "")
                    tile.display()
                except Exception:
                    pass
        except Exception:
            pass

    def _update_badge(self):
        try:
            self._set_app_badge(self.unread_alerts)
            if platform.system() != "Darwin":
                if self.unread_alerts > 0:
                    self.title(f"{self._base_title} [{self.unread_alerts}]")
                else:
                    self.title(self._base_title)
        except Exception:
            pass

    def _play_alert_sound(self):
        try:
            system = platform.system()
            # Try Tk bell first (cross-platform minimal beep)
            try:
                self.bell()
            except Exception:
                pass
            if system == "Darwin":
                # Play a system sound (Glass) if available
                subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
            elif system == "Windows":
                try:
                    import winsound  # type: ignore
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
            else:
                # Linux attempts
                if shutil.which("paplay") and os.path.exists("/usr/share/sounds/freedesktop/stereo/complete.oga"):
                    subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"], check=False)
                elif shutil.which("aplay") and os.path.exists("/usr/share/sounds/alsa/Front_Center.wav"):
                    subprocess.run(["aplay", "/usr/share/sounds/alsa/Front_Center.wav"], check=False)
        except Exception:
            pass

    def _flash_taskbar(self, start: bool):
        try:
            if platform.system() == "Windows":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = self.winfo_id()

                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_uint),
                        ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint),
                    ]

                FLASHW_STOP = 0
                FLASHW_CAPTION = 0x00000001
                FLASHW_TRAY = 0x00000002
                FLASHW_ALL = FLASHW_CAPTION | FLASHW_TRAY
                FLASHW_TIMERNOFG = 0x0000000C

                flags = FLASHW_ALL | FLASHW_TIMERNOFG if start else FLASHW_STOP
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, flags, 0, 0)
                user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def _remove_selected_notifications(self):
        items = self.tree.selection()
        if not items:
            messagebox.showinfo("No Selection", "Please select one or more IPOs to remove notifications.")
            return
        removed = 0
        for item in items:
            idx = self.tree.index(item)
            if idx < len(self._filtered):
                ipo = self._filtered[idx]
                # Remove any scheduled notifications regardless of checkbox state
                key = f"{ipo.company}_{ipo.symbol}"
                existed = key in self._notification_scheduler.scheduled_notifications
                self._notification_scheduler.remove_ipo_notification(ipo)
                # Always clear the checkbox (notify flag)
                was_enabled = ipo.notify_enabled
                ipo.notify_enabled = False
                if existed or was_enabled:
                    removed += 1
                self._update_tree_item(item, ipo)
        try:
            self._notification_scheduler.save_notifications()
        except Exception:
            pass
        self._refresh_selected_panel()
        if removed > 0:
            messagebox.showinfo("Removed", f"Removed notifications for {removed} IPO(s).")
        else:
            messagebox.showinfo("Removed", "No scheduled notifications found for the selected IPO(s).")

    def _remove_all_notifications(self):
        count = 0
        # Remove from scheduler and disable flags
        for ipo in self._data:
            if ipo.notify_enabled:
                count += 1
            ipo.notify_enabled = False
        self._notification_scheduler.scheduled_notifications.clear()
        try:
            self._notification_scheduler.save_notifications()
        except Exception:
            pass
        # Refresh the view
        self.apply_filters()
        self._refresh_selected_panel()
        messagebox.showinfo("Removed", f"Removed notifications for {count} IPO(s).")

    def refresh_data(self) -> None:
        if self._fetch_thread and self._fetch_thread.is_alive():
            return
        self.status_var.set("Fetching from IPO Scoop...")
        self.refresh_btn.configure(state=tk.DISABLED)
        self._fetch_thread = threading.Thread(target=self._do_fetch, daemon=True)
        self._fetch_thread.start()
        self.after(100, self._check_fetch)

    def _do_fetch(self) -> None:
        data, err = fetch_ipos_from_iposcoop2()
        self._fetch_queue.put((data, err, "IPO Scoop"))

    def _check_fetch(self) -> None:
        try:
            data, err, source = self._fetch_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._check_fetch)
            return
        finally:
            self.refresh_btn.configure(state=tk.NORMAL)
        if err:
            self.status_var.set(err)
            self._data = []
            self._filtered = []
            self._populate_table([])
            return
        self._data = data
        self.status_var.set("IPOs loaded. Categorizing companies...")
        self._populate_table(self._data)
        self.start_categorization()

    def start_categorization(self):
        if self._cat_thread and self._cat_thread.is_alive():
            return
        self._cat_thread = threading.Thread(target=self.categorize_companies, daemon=True)
        self._cat_thread.start()
        self.after(800, self._check_categorization)

    def categorize_companies(self):
        for ipo in self._data:
            if ipo.company and ipo.category == "?":
                quick_info = get_quick_info(ipo.company)
                ipo._info_snippet = quick_info
                ipo.category = categorize_fast(ipo.company, quick_info)
        self._cat_queue.put(None)

    def _check_categorization(self):
        try:
            _ = self._cat_queue.get_nowait()
            self.status_var.set(f"Loaded and categorized {len(self._data)} IPOs.")
            self._update_category_filter()
            self.apply_filters()
        except queue.Empty:
            self.status_var.set("Updating categories...")
            self.after(1000, self._check_categorization)

    def _update_category_filter(self):
        found = set(i.category for i in self._data)
        # Build the category list with proper ordering
        values = ["All"]
        # Add found categories that are in the official list
        values += sorted([c for c in (found & set(CATEGORIES)) if c != "?"])
        # Add special categories if found
        if "Cannot Categorize" in found:
            values.append("Cannot Categorize")
        if "Unknown" in found:
            values.append("Unknown")
        self.cat_cb.configure(values=values)
        if self.cat_var.get() not in values:
            self.cat_var.set("All")

    def apply_filters(self) -> None:
        term = self.search_var.get().strip().lower()
        cat = self.cat_var.get()

        def match(i: IPO) -> bool:
            if cat and cat != "All" and i.category != cat:
                return False
            hay = f"{i.company} {i.symbol} {i.lead_managers} {i.shares_m} {i.price_low} {i.price_high} {i.est_volume} {i.exp_trade} {i.scoop_rating} {i.rating_change} {i.category}".lower()
            return term in hay

        self._filtered = [i for i in self._data if match(i)]
        self._populate_table(self._filtered)

    def _populate_table(self, data: List[IPO]) -> None:
        self.tree.delete(*self.tree.get_children())
        for ipo in data:
            notify_symbol = "✓" if ipo.notify_enabled else ""
            values = (
                notify_symbol,
                "🔍",  # Analyze icon
                ipo.company,
                ipo.symbol,
                ipo.lead_managers,
                ipo.shares_m,
                ipo.price_low,
                ipo.price_high,
                ipo.est_volume,
                ipo.exp_trade,
                ipo.scoop_rating,
                ipo.rating_change,
                ipo.category,
            )
            self.tree.insert("", tk.END, values=values)

    def sort_by(self, column: str) -> None:
        if not self._filtered:
            return
        self._sort_state[column] = not self._sort_state.get(column, False)
        reverse = self._sort_state[column]
        self._filtered.sort(key=lambda i: i.get_sort_key(column), reverse=reverse)
        self._populate_table(self._filtered)
        self.status_var.set(f"Sorted by {column} {'desc' if reverse else 'asc'}")


def categorize_with_confidence(company_name: str, description: str = "") -> Tuple[str, float]:
    """
    Categorize a company with confidence scoring.
    Returns tuple of (category, confidence_score)
    Confidence score is between 0 and 1.
    """
    text = f"{company_name} {description}".lower()
    category_scores: Dict[str, int] = {}
    
    # Enhanced keywords with weights for more accurate categorization
    enhanced_keywords = {
        "Financial": {
            "strong": ["bank", "banking", "investment bank", "hedge fund", "private equity", "venture capital", "asset management"],
            "medium": ["financial", "capital", "investment", "fund", "asset", "insurance", "lending", "trust", "credit", "finance"],
            "weak": ["wealth", "securities", "equity", "trading", "portfolio"]
        },
        "Pharmaceutical": {
            "strong": ["pharmaceutical", "pharma", "drug development", "clinical trials", "fda", "biotech", "biopharmaceutical"],
            "medium": ["drug", "therapeutic", "clinical", "medicine", "biotechnology", "life sciences", "diagnostics"],
            "weak": ["health", "medical", "vaccines", "treatment", "therapy"]
        },
        "AI": {
            "strong": ["artificial intelligence", "machine learning", "deep learning", "neural network", "nlp", "computer vision"],
            "medium": ["ai", "intelligence", "analytics", "algorithm", "data science", "predictive"],
            "weak": ["tech", "data", "software", "platform", "digital", "cloud"]
        },
        "Engineering": {
            "strong": ["engineering firm", "civil engineering", "mechanical engineering", "electrical engineering", "structural"],
            "medium": ["engineering", "construction", "infrastructure", "industrial", "manufacturing", "systems"],
            "weak": ["solutions", "technology", "automation", "design", "technical"]
        },
        "Defense": {
            "strong": ["defense contractor", "military", "aerospace defense", "weapons", "missile systems", "radar systems"],
            "medium": ["defense", "aerospace", "security", "government contractor", "aviation", "surveillance"],
            "weak": ["tactical", "strategic", "national security", "homeland"]
        },
        "Robotics": {
            "strong": ["robotics", "industrial robots", "automation systems", "robotic process", "autonomous systems"],
            "medium": ["robots", "automation", "autonomous", "drone", "robotic", "automated"],
            "weak": ["mechanical", "control systems", "sensors"]
        },
        "Services": {
            "strong": ["consulting firm", "professional services", "business services", "managed services", "outsourcing"],
            "medium": ["services", "consulting", "support", "management", "logistics", "advisory"],
            "weak": ["solutions", "staffing", "operations", "administration"]
        },
        "Communications": {
            "strong": ["telecommunications", "telecom", "wireless carrier", "network provider", "isp", "broadband provider"],
            "medium": ["communications", "network", "wireless", "mobile", "broadband", "internet", "connectivity"],
            "weak": ["satellite", "fiber", "5g", "data transmission"]
        },
        "Materials": {
            "strong": ["materials science", "chemical company", "steel manufacturer", "mining company", "metals producer"],
            "medium": ["materials", "chemical", "metals", "steel", "aluminum", "polymer", "composite", "mining"],
            "weak": ["industrial", "specialty", "minerals", "raw materials"]
        },
        "Quantum": {
            "strong": ["quantum computing", "quantum technology", "quantum processor", "quantum cryptography", "qubit"],
            "medium": ["quantum", "quantum mechanics", "quantum research", "quantum systems"],
            "weak": ["computing", "research", "physics", "advanced technology"]
        },
        "Transportation": {
            "strong": ["automotive manufacturer", "vehicle manufacturer", "electric vehicle", "autonomous vehicle", "transportation company"],
            "medium": ["automotive", "vehicle", "car", "transportation", "mobility", "auto", "truck", "ev"],
            "weak": ["battery", "charging", "fleet", "logistics"]
        },
        "Raw Products": {
            "strong": ["mining company", "oil company", "gas producer", "energy extraction", "commodity producer"],
            "medium": ["mining", "energy", "oil", "gas", "coal", "agriculture", "commodities", "resources"],
            "weak": ["natural", "extraction", "drilling", "production"]
        },
        "Consumer Goods": {
            "strong": ["consumer goods company", "retail brand", "fmcg", "consumer products manufacturer"],
            "medium": ["consumer", "retail", "brand", "products", "goods", "marketplace", "e-commerce"],
            "weak": ["fashion", "food", "beverage", "personal care"]
        },
        "Industrials": {
            "strong": ["industrial manufacturer", "heavy machinery", "industrial equipment", "manufacturing company"],
            "medium": ["industrial", "machinery", "equipment", "manufacturing", "infrastructure"],
            "weak": ["aerospace", "defense", "construction", "engineering", "logistics"]
        },
        "Consumer Products": {
            "strong": ["consumer products company", "household products", "personal care products", "consumer brand"],
            "medium": ["consumer", "products", "retail", "brand", "household", "personal", "apparel"],
            "weak": ["footwear", "accessories", "toys", "leisure", "recreation"]
        },
        "Healthcare": {
            "strong": ["healthcare provider", "hospital system", "medical device company", "health insurance", "healthcare services"],
            "medium": ["healthcare", "health", "medical", "hospital", "clinic", "diagnostic", "therapeutic"],
            "weak": ["pharmaceutical", "biotech", "devices", "equipment", "care"]
        },
        "Information Technology": {
            "strong": ["software company", "it services", "technology company", "saas", "cloud computing", "cybersecurity"],
            "medium": ["information", "technology", "software", "hardware", "semiconductor", "internet", "cloud"],
            "weak": ["data", "cyber", "it", "tech", "digital", "computing"]
        },
        "Real Estate": {
            "strong": ["real estate company", "reit", "property developer", "real estate investment", "property management"],
            "medium": ["real estate", "property", "residential", "commercial", "development", "mortgage"],
            "weak": ["construction", "housing", "building", "estate", "land"]
        },
        "Energy": {
            "strong": ["energy company", "renewable energy", "solar company", "wind energy", "power generation", "oil and gas"],
            "medium": ["energy", "oil", "gas", "renewable", "solar", "wind", "power", "electricity"],
            "weak": ["utility", "petroleum", "natural gas", "clean energy"]
        },
        "Utilities": {
            "strong": ["utility company", "electric utility", "water utility", "gas utility", "power company"],
            "medium": ["utility", "utilities", "electric", "gas", "water", "power", "distribution"],
            "weak": ["energy", "transmission", "infrastructure", "public"]
        },
        "Distributor": {
            "strong": ["distributor", "wholesale distributor", "distribution company", "supply chain company", "logistics provider"],
            "medium": ["distribution", "wholesale", "supply chain", "logistics", "fulfillment", "warehousing"],
            "weak": ["supplier", "vendor", "channel", "dealer", "trading", "import", "export"]
        },
        "Reseller": {
            "strong": ["reseller", "retailer", "retail company", "e-commerce platform", "marketplace operator"],
            "medium": ["resale", "merchant", "broker", "agent", "marketplace", "platform", "aggregator"],
            "weak": ["intermediary", "sales", "commerce", "shop", "store", "dealer"]
        }
    }
    
    # Calculate scores with weighted keywords
    for category, keyword_groups in enhanced_keywords.items():
        score = 0
        for keyword in keyword_groups.get("strong", []):
            if keyword in text:
                score += 10 if keyword in company_name.lower() else 5
        for keyword in keyword_groups.get("medium", []):
            if keyword in text:
                score += 5 if keyword in company_name.lower() else 3
        for keyword in keyword_groups.get("weak", []):
            if keyword in text:
                score += 2 if keyword in company_name.lower() else 1
        
        if score > 0:
            category_scores[category] = score
    
    if not category_scores:
        return "Cannot Categorize", 0.0
    
    # Get the top category and calculate confidence
    sorted_scores = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
    top_category, top_score = sorted_scores[0]
    
    # Calculate confidence based on score distribution
    total_score = sum(score for _, score in sorted_scores)
    confidence = top_score / total_score if total_score > 0 else 0
    
    # Additional confidence boost if top score is significantly higher than second
    if len(sorted_scores) > 1:
        second_score = sorted_scores[1][1]
        if top_score > second_score * 2:
            confidence = min(confidence * 1.2, 1.0)
    
    # Require minimum confidence threshold
    min_confidence = 0.4  # 40% confidence minimum
    if confidence < min_confidence or top_score < 5:
        return "Cannot Categorize", confidence
    
    return top_category, confidence


def categorize_fast(company_name: str, description: str = "") -> str:
    """
    Categorization function with special rules for certain company name patterns.
    Returns "Cannot Categorize" if confidence is too low.
    """
    # Special rules based on company name patterns
    company_lower = company_name.lower()
    
    # Check for "Spirits" -> "Distributor" (not "Distribution" as that's not in our categories)
    if "spirits" in company_lower:
        return "Distributor"
    
    # Check for health/therapeutic indicators -> "Healthcare"
    healthcare_indicators = ["health", "therapeutics", "therapeutic"]
    for indicator in healthcare_indicators:
        if indicator in company_lower:
            return "Healthcare"
    
    # Check for financial indicators -> "Financial"
    financial_indicators = ["crown", "holding", "holdings", "acquisition", "acquisitions", "partners"]
    for indicator in financial_indicators:
        if indicator in company_lower:
            return "Financial"
    
    # If no special rules apply, use the confidence-based system
    category, confidence = categorize_with_confidence(company_name, description)
    return category


def get_quick_info(company: str) -> str:
    """Enhanced company information gathering using web search and page scraping."""
    info_sources: List[str] = []
    
    # Try DuckDuckGo API first
    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(company)}&format=json&no_html=1"
        r = requests.get(url, timeout=3, headers={'User-Agent': USER_AGENT})
        data = r.json()
        if data.get("AbstractText"):
            info_sources.append(data["AbstractText"][:500])
    except Exception:
        pass
    
    # Try Wikipedia
    try:
        wiki_info = get_wikipedia_summary(company)
        if wiki_info:
            info_sources.append(wiki_info[:600])
    except Exception:
        pass
    
    # Try web search and scraping if BeautifulSoup is available
    if HAS_BS4 and len(info_sources) < 2:
        try:
            web_info = search_company_web(company)
            if web_info:
                info_sources.append(web_info[:800])
        except Exception:
            pass
    
    # Combine all sources, prioritizing diversity of information
    combined = ' '.join(info_sources)
    return combined[:1500] if combined else ""


def search_company_web(company: str) -> str:
    """
    Search for company information on the web using multiple sources.
    Uses BeautifulSoup to parse and extract relevant content from web pages.
    """
    if not HAS_BS4:
        return ""
    
    info_parts = []
    
    # Try to search using DuckDuckGo HTML search
    try:
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(company + ' company profile business')}"
        search_content = http_get(search_url, timeout=5)
        soup = BeautifulSoup(search_content, 'html.parser')
        
        # Extract search result snippets
        results = soup.find_all('a', class_='result__snippet')[:3]  # Get top 3 results
        for result in results:
            text = result.get_text(strip=True)
            if text and len(text) > 50:
                info_parts.append(text[:200])
    except Exception:
        pass
    
    # Try to get company info from Bloomberg
    try:
        bloomberg_search = f"https://www.bloomberg.com/search?query={urllib.parse.quote(company)}"
        # Note: Bloomberg might block automated requests, so this is a fallback
        content = requests.get(bloomberg_search, timeout=5, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }).text
        soup = BeautifulSoup(content, 'html.parser')
        
        # Look for description meta tags or article snippets
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            info_parts.append(meta_desc['content'][:300])
    except Exception:
        pass
    
    # Try Reuters company search
    try:
        reuters_url = f"https://www.reuters.com/site-search/?query={urllib.parse.quote(company)}"
        content = requests.get(reuters_url, timeout=5, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }).text
        soup = BeautifulSoup(content, 'html.parser')
        
        # Extract article descriptions or summaries
        articles = soup.find_all(['p', 'div'], class_=re.compile(r'(description|summary|excerpt)'))[:2]
        for article in articles:
            text = article.get_text(strip=True)
            if text and len(text) > 50:
                info_parts.append(text[:250])
    except Exception:
        pass
    
    # Try to get info from Yahoo Finance
    try:
        # First try with the ticker symbol if available
        yahoo_url = f"https://finance.yahoo.com/lookup?s={urllib.parse.quote(company)}"
        content = requests.get(yahoo_url, timeout=5, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }).text
        soup = BeautifulSoup(content, 'html.parser')
        
        # Look for company description
        desc_elements = soup.find_all(['p', 'div', 'span'], string=re.compile(r'.{50,}'))[:3]
        for elem in desc_elements:
            text = elem.get_text(strip=True)
            if company.lower() in text.lower() and len(text) > 50:
                info_parts.append(text[:300])
                break
    except Exception:
        pass
    
    # Try MarketWatch
    try:
        mw_url = f"https://www.marketwatch.com/search?q={urllib.parse.quote(company)}&ts=0&tab=All%20News"
        content = requests.get(mw_url, timeout=5, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }).text
        soup = BeautifulSoup(content, 'html.parser')
        
        # Extract article descriptions
        articles = soup.find_all(['div', 'p'], class_=re.compile(r'article'))[:2]
        for article in articles:
            text = article.get_text(strip=True)
            if text and len(text) > 50:
                info_parts.append(text[:250])
    except Exception:
        pass
    
    # Combine and clean the collected information
    combined_info = ' '.join(info_parts)
    
    # Remove duplicate sentences and clean up
    sentences = combined_info.split('. ')
    unique_sentences = []
    seen = set()
    
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and sentence.lower() not in seen and len(sentence) > 20:
            seen.add(sentence.lower())
            unique_sentences.append(sentence)
    
    final_info = '. '.join(unique_sentences[:5])  # Keep top 5 unique sentences
    return final_info[:1000] if final_info else ""


def get_wikipedia_summary(company: str) -> str:
    try:
        api_url = (
            "https://en.wikipedia.org/w/api.php?action=query&format=json&prop=extracts&exintro=1&explaintext=1&redirects=1&titles="
            + urllib.parse.quote(company) + "&formatversion=2"
        )
        content = http_get(api_url, timeout=4)
        data = json.loads(content.decode('utf-8', errors='ignore'))
        pages = data.get("query", {}).get("pages", [])
        if pages:
            page = pages[0]
            if not page.get("missing") and page.get("extract"):
                extract = page["extract"]
                sentences = extract.split('. ')
                return ('. '.join(sentences[:3]) + '.') if len(sentences) >= 2 else extract[:400]
        for suffix in [" company", " corporation", " inc"]:
            try:
                api_url = (
                    "https://en.wikipedia.org/w/api.php?action=query&format=json&prop=extracts&exintro=1&explaintext=1&redirects=1&titles="
                    + urllib.parse.quote(company + suffix) + "&formatversion=2"
                )
                content = http_get(api_url, timeout=3)
                data = json.loads(content.decode('utf-8', errors='ignore'))
                pages = data.get("query", {}).get("pages", [])
                if pages:
                    page = pages[0]
                    if not page.get("missing") and page.get("extract"):
                        extract = page["extract"]
                        sentences = extract.split('. ')
                        return ('. '.join(sentences[:3]) + '.') if len(sentences) >= 2 else extract[:400]
            except Exception:
                continue
        return ""
    except Exception:
        return ""


def main() -> None:
    app = IPOTrackerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
