"""Empty-zone soak geometry tests (Phase 3, Step 3.6 criterion 2).

``largest_empty_rectangle`` is a histogram-scan maximal rectangle — the
classic off-by-one minefield — so its edge cases are pinned here before
any 30-minute soak trusts it.
"""

from __future__ import annotations

from tools.zone_fp_soak import (
    GRID_COLS,
    GRID_ROWS,
    largest_empty_rectangle,
    occupied_grid,
    rectangle_to_polygon,
)


def grid(paint: set[tuple[int, int]]) -> list[list[bool]]:
    return occupied_grid(paint)


def test_fully_occupied_grid_has_no_empty_rectangle():
    all_cells = {(col, row) for col in range(GRID_COLS) for row in range(GRID_ROWS)}
    assert largest_empty_rectangle(grid(all_cells)) is None


def test_fully_empty_grid_returns_everything():
    rect = largest_empty_rectangle(grid(set()))
    assert rect == (0, 0, GRID_ROWS, GRID_COLS)


def test_single_occupied_cell_shrinks_to_rest_of_row():
    paint = {(5, 7)}
    rect = largest_empty_rectangle(grid(paint))
    rows, cols = rect[2], rect[3]
    assert rows * cols == 18 * GRID_ROWS  # full height × cols 6-23 beats the split bands


def test_blocked_column_splits_rows():
    paint = {(12, row) for row in range(GRID_ROWS)}
    rect = largest_empty_rectangle(grid(paint))
    rows, cols = rect[2], rect[3]
    assert rows * cols == 12 * GRID_ROWS  # left block (cols 0-11) is the wider half


def test_l_shape_prefers_wide_over_tall_when_wider():
    paint = {(col, 13) for col in range(4, GRID_COLS)}
    paint |= {(3, row) for row in range(0, 13)}
    rect = largest_empty_rectangle(grid(paint))
    row, col, rows, cols = rect
    assert cols > rows
    assert (row + rows) <= 13 and col >= 4  # inside the wide arm, clear of both walls


def test_polygon_is_normalized_and_inside_rect_bounds():
    rect = (2, 3, 5, 7)
    polygon = rectangle_to_polygon(rect, width=1200, height=600)
    (x1, y1), (x2, _), (_, y2), _ = polygon
    assert 0.0 <= x1 < x2 <= 1.0
    assert 0.0 <= y1 < y2 <= 1.0
    col_width = 1.0 / GRID_COLS
    row_height = 1.0 / GRID_ROWS
    assert x1 > 3 * col_width and x2 < 10 * col_width
    assert y1 > 2 * row_height and y2 < 7 * row_height
