# Grade: D — correct output, but worst-case O(n²) time and the outer loop never
# short-circuits, so even an already-sorted input pays the full cost.
# Space is O(n) for the copy; the inner loop itself is O(1) auxiliary.
#
# Run: code-explain --analyze e2e_tests/06_python_bubble_sort.py


def bubble_sort(items: list[int]) -> list[int]:
    """Sort a list of integers in ascending order using bubble sort."""
    n = len(items)
    result = items[:]           # O(n) copy — fine, but worth noting
    for i in range(n):          # outer pass: runs n times unconditionally
        for j in range(n - 1):  # inner pass: O(n) comparisons per outer step → O(n²) total
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
            # No early-exit flag: a fully-sorted array still completes all n² iterations
    return result
