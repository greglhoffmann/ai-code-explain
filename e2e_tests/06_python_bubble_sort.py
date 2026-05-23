def bubble_sort(items: list[int]) -> list[int]:
    """Sort a list of integers in ascending order using bubble sort."""
    n = len(items)
    result = items[:]
    for i in range(n):
        for j in range(n - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
    return result
