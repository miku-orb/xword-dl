import datetime
import urllib.parse

import puz
import requests
from getpass import getpass

from .basedownloader import BaseDownloader
from ..util import XWordDLException, join_bylines, update_config_file, unidecode


class NewYorkTimesDownloader(BaseDownloader):
    command = "nyt"
    outlet = "New York Times"
    outlet_prefix = "NY Times"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.url_from_id = "https://www.nytimes.com/svc/crosswords/v2/puzzle/{}.json"
        self.url_from_date = (
            "https://www.nytimes.com/svc/crosswords/v6/puzzle/daily/{}.json"
        )

        if "url" in kwargs and not self.date:
            self.date = self.parse_date_from_url(kwargs.get("url"))

        self.headers = {}
        self.cookies = {}

        username = self.settings.get("username")
        password = self.settings.get("password")

        if username and password:
            self.authenticate(username, password)
        nyts_token = self.settings.get("NYT_S")

        if not nyts_token:
            raise XWordDLException(
                "No credentials provided or stored. Try running xword-dl nyt --authenticate"
            )
        else:
            self.cookies.update({"NYT-S": nyts_token})

    @classmethod
    def matches_url(cls, url_components):
        return (
            "nytimes.com" in url_components.netloc
            and "crosswords/game/daily" in url_components.path
        )

    @classmethod
    def authenticate(cls, username, password):
        """Given a NYT username and password, returns the NYT-S cookie value"""

        username = username or input("New York Times username: ")
        password = password or getpass("Password: ")

        try:
            res = requests.post(
                "https://myaccount.nytimes.com/svc/ios/v2/login",
                data={"login": username, "password": password},
                headers={
                    "User-Agent": "Crossword/1844.220922 CFNetwork/1335.0.3 Darwin/21.6.0",
                    "client_id": "ios.crosswords",
                },
            )

            res.raise_for_status()
        except requests.HTTPError:
            raise XWordDLException(
                "Unable to authenticate with NYT servers. You can try manually adding an authenticated NYT-S token to your xword-dl config file. More information here: https://github.com/thisisparker/xword-dl/issues/51"
            )

        nyts_token = ""

        for cookie in res.json()["data"]["cookies"]:
            if cookie["name"] == "NYT-S":
                nyts_token = cookie["cipheredValue"]

        if nyts_token:
            update_config_file("nyt", {"NYT-S": nyts_token})
        else:
            raise XWordDLException("NYT-S cookie not found.")

    def parse_date_from_url(self, url):
        path = urllib.parse.urlparse(url).path
        date_string = "".join(path.split("/")[-3:])

        return datetime.datetime.strptime(date_string, "%Y%m%d")

    def find_latest(self):
        oracle = "https://www.nytimes.com/svc/crosswords/v2/oracle/daily.json"

        res = requests.get(oracle)
        puzzle_date = res.json()["results"]["current"]["print_date"]

        url = self.url_from_date.format(puzzle_date)

        return url

    def find_by_date(self, dt):
        return self.url_from_date.format(dt.strftime("%Y-%m-%d"))

    def find_solver(self, url):
        if not url.endswith(".json"):
            url = self.find_by_date(self.parse_date_from_url(url))

        return url

    def fetch_data(self, solver_url):
        res = requests.get(solver_url, cookies=self.cookies)

        try:
            res.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if res.status_code == 403:
                raise XWordDLException(
                    "Puzzle data not available. Try re-authenticating with xword-dl nyt --authenticate"
                )
            elif res.status_code == 404:
                raise XWordDLException("Puzzle data not found.")
            else:
                raise XWordDLException("HTTP error:", e)

        xword_data = res.json()
        return xword_data

    def parse_xword(self, xw_data):
        puzzle = puz.Puzzle()

        puzzle.author = join_bylines(xw_data["constructors"], "and").strip()
        puzzle.copyright = xw_data["copyright"]
        puzzle.height = int(xw_data["body"][0]["dimensions"]["height"])
        puzzle.width = int(xw_data["body"][0]["dimensions"]["width"])

        if not self.date:
            self.date = datetime.datetime.strptime(
                xw_data["publicationDate"], "%Y-%m-%d"
            )

        puzzle.title = xw_data.get("title") or self.date.strftime("%A, %B %d, %Y")

        if xw_data.get("notes"):
            puzzle.notes = xw_data.get("notes")[0]["text"]

        solution = ""
        fill = ""
        markup = b""
        rebus_board = []
        rebus_index = 0
        rebus_table = ""

        for idx, square in enumerate(xw_data["body"][0]["cells"]):
            if not square:
                solution += "."
                fill += "."
                rebus_board.append(0)
            elif square and len(square.get("answer", "")) == 1:
                solution += square["answer"]
                fill += "-"
                rebus_board.append(0)
            else:
                try:
                    suitable_answer = unidecode(
                        square.get("answer") or square["moreAnswers"]["valid"][0]
                    )
                except (IndexError, KeyError):
                    raise XWordDLException(
                        "Unable to parse puzzle JSON. Possibly something .puz incompatible"
                    )

                solution += suitable_answer[0]
                fill += "-"
                rebus_board.append(rebus_index + 1)
                rebus_table += "{:2d}:{};".format(rebus_index, suitable_answer)
                rebus_index += 1

            markup += b"\x00" if square.get("type", 1) == 1 else b"\x80"

        puzzle.solution = solution
        puzzle.fill = fill

        if b"\x80" in markup:
            puzzle.extensions[b"GEXT"] = markup
            puzzle._extensions_order.append(b"GEXT")
            puzzle.markup()

        if any(rebus_board):
            puzzle.extensions[b"GRBS"] = bytes(rebus_board)
            puzzle.extensions[b"RTBL"] = rebus_table.encode(puz.ENCODING)
            puzzle._extensions_order.extend([b"GRBS", b"RTBL"])
            puzzle.rebus()

        clue_list = xw_data["body"][0]["clues"]
        clue_list.sort(key=lambda c: (int(c["label"]), c["direction"]))

        puzzle.clues = [c["text"][0].get("plain") or "" for c in clue_list]

        return puzzle

    def pick_filename(self, puzzle, **kwargs):
        if puzzle.title == self.date.strftime("%A, %B %d, %Y"):
            title = ""
        else:
            title = puzzle.title

        return super().pick_filename(puzzle, title=title, **kwargs)


class NewYorkTimesVarietyDownloader(NewYorkTimesDownloader):
    command = "nytv"
    outlet = "New York Times Variety"
    outlet_prefix = "NY Times Variety"

    def __init__(self, **kwargs):
        super().__init__(inherit_settings="nyt", **kwargs)

        self.url_from_date = (
            "https://www.nytimes.com/svc/crosswords/v6/puzzle/variety/{}.json"
        )

    def parse_xword(self, xw_data):
        try:
            return super().parse_xword(xw_data)
        except ValueError:
            raise XWordDLException(
                "Encountered error while parsing data. Maybe the selected puzzle is not a crossword?"
            )


class NewYorkTimesMiniDownloader(NewYorkTimesDownloader):
    command = "nytm"
    outlet = "New York Times Mini"
    outlet_prefix = "NY Times Mini"

    def __init__(self, **kwargs):
        super().__init__(inherit_settings="nyt", **kwargs)

        self.url_from_date = (
            "https://www.nytimes.com/svc/crosswords/v6/puzzle/mini/{}.json"
        )

    @classmethod
    def matches_url(cls, url_components):
        return "nytimes.com" in url_components.netloc and "mini" in url_components.path

    def find_latest(self):
        oracle = "https://www.nytimes.com/svc/crosswords/v2/oracle/mini.json"

        res = requests.get(oracle)
        puzzle_date = res.json()["results"]["current"]["print_date"]

        url = self.url_from_date.format(puzzle_date)

        return url
