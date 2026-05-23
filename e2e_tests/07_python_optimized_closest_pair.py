from __future__ import annotations

from math import hypot
from typing import List, Tuple

Point = Tuple[float, float]


def closest_pair(points: List[Point]) -> tuple[Point, Point, float]:
    """Return the closest pair of points and their Euclidean distance.

    Uses a classic divide-and-conquer algorithm.
    """
    if len(points) < 2:
        raise ValueError("At least two points are required")

    px = sorted(points, key=lambda p: (p[0], p[1]))
    py = sorted(points, key=lambda p: (p[1], p[0]))

    p1, p2, dist = _closest_pair_recursive(px, py)
    return p1, p2, dist


def _closest_pair_recursive(px: List[Point], py: List[Point]) -> tuple[Point, Point, float]:
    n = len(px)

    if n <= 3:
        best = (px[0], px[1], _distance(px[0], px[1]))
        for i in range(n):
            for j in range(i + 1, n):
                d = _distance(px[i], px[j])
                if d < best[2]:
                    best = (px[i], px[j], d)
        return best

    mid = n // 2
    mid_x = px[mid][0]

    left_x = px[:mid]
    right_x = px[mid:]

    left_set = set(left_x)
    left_y: List[Point] = []
    right_y: List[Point] = []
    for point in py:
        if point in left_set:
            left_y.append(point)
        else:
            right_y.append(point)

    l1, l2, d_left = _closest_pair_recursive(left_x, left_y)
    r1, r2, d_right = _closest_pair_recursive(right_x, right_y)

    if d_left <= d_right:
        best_pair = (l1, l2)
        delta = d_left
    else:
        best_pair = (r1, r2)
        delta = d_right

    strip = [p for p in py if abs(p[0] - mid_x) < delta]

    for i in range(len(strip)):
        for j in range(i + 1, min(i + 8, len(strip))):
            d = _distance(strip[i], strip[j])
            if d < delta:
                delta = d
                best_pair = (strip[i], strip[j])

    return best_pair[0], best_pair[1], delta


def _distance(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])
