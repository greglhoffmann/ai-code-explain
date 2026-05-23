from typing import Dict, List, Optional


def find_common_elements(list_a: List[int], list_b: List[int]) -> List[int]:
    """Return elements present in both lists, without duplicates."""
    common = []
    for item in list_a:
        if item in list_b and item not in common:
            common.append(item)
    return common


def group_by_first_letter(words: List[str]) -> Dict[str, List[str]]:
    """Bucket words by their first character."""
    groups: Dict[str, List[str]] = {}
    for word in words:
        key = word[0].lower()
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return groups


def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number (0-indexed)."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


def read_large_file_lines(path: str) -> List[str]:
    """Return all non-empty, non-whitespace lines from a file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.split("\n")
    return [line for line in lines if line.strip()]


def count_word_frequencies(text: str) -> Dict[str, int]:
    """Return a frequency map of every word in text."""
    words = text.lower().split()
    freq: Dict[str, int] = {}
    for word in words:
        freq[word] = freq.get(word, 0) + 1
    return freq


def has_duplicates(items: List) -> bool:
    """Return True if any value appears more than once."""
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
            new_r = r.copy()
            new_r["value"] = round(r["value"] * (1 - rate), 2)
            result.append(new_r)
    return result


def is_palindrome(s: str) -> bool:
    """Return True if s reads the same forwards and backwards (case-insensitive)."""
    cleaned = ""
    for ch in s:
        if ch.isalnum():
            cleaned += ch.lower()
    return cleaned == cleaned[::-1]
