# Grade: C — correct code that runs without errors, but full of well-known
# Python antipatterns that will bite you at scale or under real workloads.
#
# Run: code-explain --analyze e2e_tests/04_python_logic_smells.py

from typing import List


# Mutable default argument — `result` is shared across ALL calls to flatten().
# Repeated calls accumulate into the same list object.
def flatten(nested, result=[]):
    for item in nested:
        if isinstance(item, list):
            flatten(item, result)
        else:
            result.append(item)
    return result


# O(n³): outer loop × inner loop × `item not in duplicates` linear scan.
def find_duplicates(items: List) -> List:
    """Return items that appear more than once."""
    duplicates = []
    for i, item in enumerate(items):
        for j, other in enumerate(items):
            if i != j and item == other and item not in duplicates:
                duplicates.append(item)
    return duplicates


class ReportManager:
    """God class — owns data loading, cleaning, summarising, and rendering.

    Violates the Single Responsibility Principle and is impossible to unit-test
    individual concerns without constructing the whole object.
    """

    def __init__(self):
        self.raw_data = None
        self.cleaned_data = None
        self.summary = None
        self.errors = []

    def load(self, filepath: str) -> None:
        try:
            with open(filepath) as f:
                self.raw_data = f.read()
        except:             # bare except — catches KeyboardInterrupt, SystemExit, MemoryError …
            pass            # and silently discards the error

    def clean(self) -> None:
        if self.raw_data == None:   # identity check should use `is None`
            return
        self.cleaned_data = self.raw_data.strip().lower()

    def summarise(self) -> None:
        words = self.cleaned_data.split()
        # dict comprehension calls words.count(w) for every word — O(n²) overall
        self.summary = {w: words.count(w) for w in words}

    def render(self) -> str:
        html = ""
        for word, count in self.summary.items():
            # repeated string concatenation in a loop — O(n²) copies
            html += f"<li>{word}: {count}</li>"
        return "<ul>" + html + "</ul>"

    def run(self, filepath: str) -> str:
        """Load → clean → summarise → render in one shot."""
        self.load(filepath)
        self.clean()
        self.summarise()
        return self.render()


# Using a class where a named tuple or dataclass would be cleaner
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __eq__(self, other):
        return self.x == other.x and self.y == other.y
        # Missing __hash__ — Point instances can't be put in sets or used as dict keys


def load_config(path: str) -> dict:
    """Read a JSON config file."""
    import json     # deferred import inside a hot path — forces re-lookup each call
    with open(path) as f:
        return json.load(f)
