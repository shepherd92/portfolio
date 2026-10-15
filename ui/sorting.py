"""Shared header sorting for flat tables and allocation trees."""

from decimal import Decimal
import re


class TreeSort:
    """Sort siblings in place, retaining selection, expansion and row identity."""

    def __init__(self, tree, *, numeric_columns=(), value=None, on_sort=None):
        self.tree = tree
        self.numeric_columns = set(numeric_columns)
        self.value = value or self._cell_value
        self.on_sort = on_sort
        self.column, self.descending = None, False
        for column in ('#0', *tree['columns']):
            tree.heading(column, command=lambda column=column: self.sort(column))

    def sort(self, column):
        self.descending = not self.descending if self.column == column else False
        self.column = column
        self.apply()

    def apply(self):
        """Restore the chosen ordering after rows are rebuilt or filtered."""
        if self.column is not None:
            self._sort_children('')
            for column in ('#0', *self.tree['columns']):
                label = self.tree.heading(column, 'text').removesuffix(' ▲').removesuffix(' ▼')
                arrow = (' ▼' if self.descending else ' ▲') if column == self.column else ''
                self.tree.heading(column, text=label + arrow)
        if self.on_sort:
            self.on_sort()

    def _cell_value(self, item, column):
        return self.tree.item(item, 'text') if column == '#0' else self.tree.set(item, column)

    def _key(self, item):
        value = self.value(item, self.column)
        text = '' if value is None else str(value).strip()
        if self.column not in self.numeric_columns:
            return text.casefold() or None
        # Displayed numbers may have grouping spaces, a percent sign or a unit suffix.
        compact = text.translate(str.maketrans('', '', ' \u00a0\u202f'))
        match = re.match(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', compact)
        return Decimal(match[0]) if match else None

    def _sort_children(self, parent):
        values, empty = [], []
        for item in self.tree.get_children(parent):
            key = self._key(item)
            if key is None:
                empty.append(item)
            else:
                values.append((key, item))
        values.sort(key=lambda pair: pair[0], reverse=self.descending)
        # Missing values stay at the bottom in both directions.
        ordered = [item for _, item in values] + empty
        for index, item in enumerate(ordered):
            self.tree.move(item, parent, index)
            self._sort_children(item)
