"""Immutable publication blocks shared by the calculation reader and export."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CalculationText:
    text: str
    role: str = 'body'


@dataclass(frozen=True)
class CalculationFormula:
    latex: str
    explanation: str = ''

    @property
    def text(self):
        return '\n'.join(filter(None, (f'${self.latex}$', self.explanation)))


@dataclass(frozen=True)
class CalculationTable:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    numeric_columns: tuple[int, ...] = ()
    total_rows: tuple[int, ...] = ()

    @property
    def text(self):
        rows = (self.columns, *self.rows)
        widths = [max(len(row[i]) for row in rows) for i in range(len(self.columns))]
        def line(row):
            return ' | '.join(value.rjust(width) if i in self.numeric_columns else value.ljust(width)
                              for i, (value, width) in enumerate(zip(row, widths)))
        separator = '-+-'.join('-' * width for width in widths)
        return '\n'.join(filter(None, (self.title, line(self.columns), separator,
                                     *(line(row) for row in self.rows))))


@dataclass(frozen=True)
class CalculationPage:
    title: str
    intro: str
    blocks: tuple[CalculationText | CalculationFormula | CalculationTable, ...]
    outcome: CalculationTable

    @property
    def text(self):
        return '\n\n'.join(filter(None, (self.title, self.intro,
                                         *(block.text for block in self.blocks), self.outcome.text)))


@dataclass(frozen=True)
class CalculationSection:
    title: str
    pages: tuple[CalculationPage, ...]

    @property
    def text(self):
        return '\n\n'.join((self.title, *(page.text for page in self.pages)))


@dataclass(frozen=True)
class CalculationReport:
    sections: tuple[CalculationSection, ...]
    caption: str = ''

    @property
    def text(self):
        return '\n\n'.join(filter(None, (self.caption, *(section.text for section in self.sections))))
