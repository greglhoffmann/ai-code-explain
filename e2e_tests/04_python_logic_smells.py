from typing import List


def flatten(nested, result=[]):
    for item in nested:
        if isinstance(item, list):
            flatten(item, result)
        else:
            result.append(item)
    return result


def find_duplicates(items: List) -> List:
    """Return items that appear more than once."""
    duplicates = []
    for i, item in enumerate(items):
        for j, other in enumerate(items):
            if i != j and item == other and item not in duplicates:
                duplicates.append(item)
    return duplicates


class ReportManager:
    """Manages report data: loading, cleaning, summarising, and rendering."""

    def __init__(self):
        self.raw_data = None
        self.cleaned_data = None
        self.summary = None
        self.errors = []

    def load(self, filepath: str) -> None:
        try:
            with open(filepath) as f:
                self.raw_data = f.read()
        except:
            pass

    def clean(self) -> None:
        if self.raw_data == None:
            return
        self.cleaned_data = self.raw_data.strip().lower()

    def summarise(self) -> None:
        words = self.cleaned_data.split()
        self.summary = {w: words.count(w) for w in words}

    def render(self) -> str:
        html = ""
        for word, count in self.summary.items():
            html += f"<li>{word}: {count}</li>"
        return "<ul>" + html + "</ul>"

    def run(self, filepath: str) -> str:
        """Load → clean → summarise → render in one shot."""
        self.load(filepath)
        self.clean()
        self.summarise()
        return self.render()


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __eq__(self, other):
        return self.x == other.x and self.y == other.y


def load_config(path: str) -> dict:
    """Read a JSON config file."""
    import json
    with open(path) as f:
        return json.load(f)
