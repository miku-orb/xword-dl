import datetime
import re

import puz

from bs4 import BeautifulSoup, Tag

from .basedownloader import BaseDownloader
from ..util import XWordDLException


class TechnopolDownloader(BaseDownloader):
    command = "technopol"
    outlet = "TECHNOPOL"
    outlet_prefix = "TECHNOPOL"

    POLISH_ALPHABET = [
        "A", "Ą", "B", "C", "Ć", "D", "E", "Ę", "F", "G", "H", "I",
        "J", "K", "L", "Ł", "M", "N", "Ń", "O", "Ó", "P", "Q", "R",
        "S", "Ś", "T", "U", "V", "W", "X", "Y", "Z", "Ż", "Ź",
    ]

    def __init__(self, **kwargs):
        super().__init__(headers={"User-Agent": "xword-dl"}, **kwargs)

    @classmethod
    def matches_url(cls, url_components):
        # no ID in URLs!
        return url_components.netloc == "technopol.pl" and url_components.path == "/krzyzowka_dnia"

    def find_latest(self):
        return "https://technopol.pl/krzyzowka_dnia"
    
    def find_solver(self, url):
        return url # no ID in URLs
    
    def fetch_data(self, solver_url):
        return self._get_technopol_puzzle(solver_url)
    
    def parse_xword(self, xw_data):
        puzzle = puz.Puzzle()
        puzzle.version = b'2.0'
        puzzle.fileversion = b'2.0\0'
        puzzle.encoding = "UTF-8"
        puzzle.title = xw_data["title"]
        puzzle.author = xw_data["publisher"]
        puzzle.copyright = xw_data["copyright"]
        puzzle.width = xw_data["dimensions"]["width"]
        puzzle.height = xw_data["dimensions"]["height"]

        solution = ""
        fill = ""
        #markup = b""

        for y in range(0, puzzle.height):
            for x in range(0, puzzle.width):
                cell = xw_data["puzzle"][y][x]
                sol_cell = xw_data["solution"][y][x]
                if cell == "#":
                    fill += "."
                    solution += "."
                    #markup += b"\x00"
                else:
                    fill += "-"
                    solution += sol_cell if sol_cell != "#" else "."
                    #markup += b"\x00"

        puzzle.solution = solution
        puzzle.fill = fill

        clue_list = (
            xw_data["clues"]["Across"] + xw_data["clues"]["Down"]
        )
        sorted_clue_list = sorted(clue_list, key=lambda x: int(x[0]))
        clues = [clue[1] for clue in sorted_clue_list]
        puzzle.clues = clues

        return puzzle

    def _make_blank_puzzle(self, width, height):
        puzzle = []
        for row in range(height):
            puzzle.append(["#"] * width)
        return puzzle

    def _get_technopol_layout(self, soup: BeautifulSoup):
        crossword = soup.find("table", id="krzyzowka")
        if not isinstance(crossword, Tag):
            raise XWordDLException("Cannot find layout for puzzle. No table tag.")
        tr = crossword.find_all("tr")
        tr0 = tr[0] if len(tr) > 0 else None
        if not all(isinstance(t, Tag) for t in tr) or len(tr) == 0 or not isinstance(tr0, Tag):
            raise XWordDLException("Cannot find layout for puzzle. No tr tags.")
        
        width = len(tr0.find_all("td"))
        height = len(tr)

        puzzle = self._make_blank_puzzle(width, height)

        y = -1
        for tr in crossword.find_all("tr"):
            if not isinstance(tr, Tag):
                raise XWordDLException("Cannot find layout for puzzle. No tr tag.")
            y = y + 1
            x = -1
            for td in tr.find_all("td"):
                if not isinstance(td, Tag) or not td.has_attr("class"):
                    raise XWordDLException("Cannot find layout for puzzle. No td tag.")
                x = x + 1
                td_class = td.get_attribute_list("class")[0]
                if td_class == "black":
                    puzzle[y][x] = "#"
                elif td_class == "white":
                    puzzle[y][x] = 0
                    wnum = td.find_all("p", class_="wnum")
                    if len(wnum):
                        num = int(wnum[0].get_text(strip=True))
                        puzzle[y][x] = num

        return puzzle

    def _get_technopol_clues(self, soup: BeautifulSoup):

        clues = dict()
        clues["Across"] = []
        clues["Down"] = []

        for direction_pair in [["poziomo_ul", "Across"], ["pionowo_ul", "Down"]]:
            direction_class = direction_pair[0]
            direction_name = direction_pair[1]
            ul = soup.find("ul", class_=direction_class)
            if not isinstance(ul, Tag):
                raise XWordDLException("Cannot find clues for puzzle. No ul tag.")
            for li in ul.find_all("li"):
                if not isinstance(li, Tag):
                    raise XWordDLException("Cannot find clues for puzzle. No li tag.")
                a = li.find("a")
                if not isinstance(a, Tag) or not a.has_attr("id"):
                    raise XWordDLException("Cannot find clues for puzzle. No a tag.")
                number = int(a.get_attribute_list("id")[0].lstrip("q"))
                text = re.sub(r"^[0-9]+.\s*", "", a.get_text(strip=True))
                clues[direction_name].append([number, text])

        return clues

    def _get_technopol_solution(self, puzzle, crossword_id: int):
        height = len(puzzle)
        width = len(puzzle[0])

        solution_puzzle = self._make_blank_puzzle(width, height)

        # This is really wack, but we'll have to brute-force the solution
        # by trying all alphabet letters and making 35 requests...
        input_nums = []

        for y in range(0, len(puzzle)):
            for x in range(0, len(puzzle[y])):
                cell = puzzle[y][x]
                if cell != "#":
                    input_num = y * width + x + 1
                    input_nums.append(input_num)

        URL_VALIDATE = "https://technopol.pl/index.php?page=krzyzowka_dnia&param=walidacja"
        for letter in self.POLISH_ALPHABET:
            request_params = dict()
            request_params["solve"] = "true"
            request_params["crossword_id"] = str(crossword_id)
            for input_num in input_nums:
                request_params[f"input{input_num}"] = letter

            response = self.session.post(URL_VALIDATE, data=request_params)
            if response.status_code != 200:
                raise XWordDLException(
                    "Cannot find solution for puzzle {} and letter {}. Response status code is {}.".format(crossword_id, letter, response.status_code)
                )
            
            bad_letters = response.json()["bad_letters"]
            for input_num in input_nums:
                if not (str(input_num) in bad_letters):
                    # this is a good letter then
                    y = (input_num - 1) // width
                    x = (input_num - 1) % width
                    solution_puzzle[y][x] = letter
            
        return solution_puzzle

    def _get_technopol_puzzle(self, url=None):
        url = url if url else "https://technopol.pl/krzyzowka_dnia"
        page = self.session.get(url)
        if page.status_code != 200:
            raise XWordDLException(
                "Cannot find puzzle at {}. Response status code is {}.".format(url, page.status_code)
            )
        soup = BeautifulSoup(page.content, "html.parser")

        form = soup.find("form", id="validate_form")
        if not isinstance(form, Tag):
            raise XWordDLException(
                "Cannot find puzzle at {}. No form tag.".format(url)
            )
        input = form.find("input", attrs={"name":"crossword_id"})
        if not isinstance(input, Tag) or not input.has_attr("value"):
            raise XWordDLException(
                "Cannot find puzzle at {}. No input tag for crossword_id.".format(url)
            )
        crossword_id = int(input.get_attribute_list("value")[0])

        puzzle = dict()
        puzzle["origin"] = "TECHNOPOL"
        puzzle["version"] = "http://ipuz.org/v2"
        puzzle["kind"] = ["http://ipuz.org/crossword"]
        puzzle["copyright"] = f"© Wydawnictwo Technopol"
        puzzle["publisher"] = "TECHNOPOL"
        puzzle["url"] = url
        puzzle["title"] = f"Krzyżówka nr {crossword_id}"
        puzzle["charset"] = "".join(self.POLISH_ALPHABET)

        puzzle["puzzle"] = self._get_technopol_layout(soup)

        height = len(puzzle["puzzle"])
        width = len(puzzle["puzzle"][0])
        puzzle["dimensions"] = dict(width=width, height=height)

        puzzle["clues"] = self._get_technopol_clues(soup)
        puzzle["solution"] = self._get_technopol_solution(puzzle["puzzle"], crossword_id)

        puzzle["org.gnome.libipuz:locale"] = "pl_PL"
        puzzle["org.gnome.libipuz:charset"] = self.POLISH_ALPHABET

        filename = f"technopol_{crossword_id}.ipuz"
        puzzle["annotation"] = filename

        return puzzle
