# Grade: B — correct, readable code that produces right answers but leaves
# obvious performance improvements on the table.
#
# Run: code-explain --analyze e2e_tests/05_python_needs_optimization.py

from typing import Dict, List, Optional


def find_common_elements(list_a: List[int], list_b: List[int]) -> List[int]:
    """Return elements present in both lists, without duplicates."""
    common = []
    for item in list_a:
        # `item in list_b` is O(n) per iteration → overall O(n²)
        # `item not in common` is another O(n) scan → overall O(n³)
        if item in list_b and item not in common:
            common.append(item)
    return common


def group_by_first_letter(words: List[str]) -> Dict[str, List[str]]:
    """Bucket words by their first character."""
    groups: Dict[str, List[str]] = {}
    for word in words:
        key = word[0].lower()
        # Explicit existence check before every insert — collections.defaultdict
        # or dict.setdefault() would eliminate the branch entirely
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return groups


def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number (0-indexed)."""
    if n <= 1:
        return n
    # Exponential time O(2^n) and stack depth O(n) — memoisation or iteration needed
    return fibonacci(n - 1) + fibonacci(n - 2)


def read_large_file_lines(path: str) -> List[str]:
    """Return all non-empty, non-whitespace lines from a file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()          # reads the whole file into memory at once
    lines = content.split("\n")
    return [line for line in lines if line.strip()]


def count_word_frequencies(text: str) -> Dict[str, int]:
    """Return a frequency map of every word in text."""
    words = text.lower().split()
    freq: Dict[str, int] = {}
    for word in words:
        # Two dict lookups (get + assignment) where += with a default would do one
        freq[word] = freq.get(word, 0) + 1
    return freq


def has_duplicates(items: List) -> bool:
    """Return True if any value appears more than once."""
    # O(n²): compares every pair — converting to a set check is O(n)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j]:
                return True
    return False


def batch_apply_discount(
    records: List[Dict], threshold: float = 100.0, rate: float = 0.10
) -> List[Dict]:
    """Return new records with a discount applied where value > threshold."""
    result = []
    for r in records:
        if r["value"] > threshold:
            # r.copy() is a shallow copy — fine here, but a list comprehension
            # with dict unpacking would be more idiomatic and slightly faster
            new_r = r.copy()
            new_r["value"] = round(r["value"] * (1 - rate), 2)
            result.append(new_r)
    return result


def is_palindrome(s: str) -> bool:
    """Return True if s reads the same forwards and backwards (case-insensitive)."""
    cleaned = ""
    for ch in s:
        if ch.isalnum():
            # String concatenation in a loop — should use a list + "".join()
            cleaned += ch.lower()
    return cleaned == cleaned[::-1]
