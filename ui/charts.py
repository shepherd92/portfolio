"""Resizable portfolio charts with scrollable legends."""

import math
import tkinter as tk
from tkinter import ttk

from ui.theme import BACKGROUND, MUTED_TEXT
from ui.widgets import AutoScrollbar


class PieChart(ttk.Frame):
    """A resizable pie with a scrollable legend; values must be nonnegative."""

    def __init__(self, parent, width=520, height=280, **kwargs):
        super().__init__(parent, width=width, height=height, **kwargs)
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1, uniform="chart")
        self.columnconfigure(1, weight=1, uniform="chart")
        self.rowconfigure(0, weight=1)
        background = ttk.Style(self).lookup("TFrame", "background") or BACKGROUND
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.legend = tk.Canvas(self, background=background, highlightthickness=0)
        self.legend.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        scrollbar = AutoScrollbar(self, orient="vertical", command=self.legend.yview)
        scrollbar.grid(row=0, column=2, sticky="ns")
        self.legend.configure(yscrollcommand=scrollbar.set)
        self._legend_frame = ttk.Frame(self.legend)
        self._legend_frame.columnconfigure(1, weight=1)
        self._legend_window = self.legend.create_window(0, 0, anchor="nw", window=self._legend_frame)
        self._legend_frame.bind("<Configure>", self._legend_size)
        self.legend.bind("<Configure>", self._legend_width)
        self.canvas.bind("<Configure>", self._draw)
        self._data, self._colors, self._fractions = {}, {}, {}
        self._empty_message = "No holdings"

    def set_data(self, values, colors, empty_message="No holdings", *, percent_values=False):
        """Normalize slice geometry; optionally label values already expressed in percent."""
        data = {str(key): float(value) for key, value in values.items()}
        if any(not math.isfinite(value) or value < 0 for value in data.values()):
            raise ValueError("Chart values must be finite and nonnegative.")
        self._data = data
        self._colors = {str(key): color for key, color in colors.items()}
        self._empty_message = empty_message
        scale = max(data.values(), default=0)
        total = math.fsum(value / scale for value in data.values()) if scale else 0
        self._fractions = {key: (value / scale / total if total else 0) for key, value in data.items()}
        for widget in self._legend_frame.winfo_children():
            widget.destroy()
        for index, key in enumerate(data):
            swatch = tk.Canvas(self._legend_frame, width=12, height=12, highlightthickness=0,
                               background=self.legend.cget('background'))
            swatch.create_rectangle(0, 0, 13, 13, fill=self._color(key), outline="")
            swatch.grid(row=index, column=0, padx=(2, 7), pady=7, sticky="n")
            label = ttk.Label(self._legend_frame, text=key, wraplength=130)
            label.grid(row=index, column=1, sticky="nw", pady=5)
            percentage = ttk.Label(self._legend_frame, text=(f"{data[key]:.1f}%" if percent_values
                                                          else f"{self._fractions[key]:.1%}"))
            percentage.grid(row=index, column=2, sticky="ne", padx=7, pady=5)
            for widget in (swatch, label, percentage):
                self._bind_scroll(widget)
        self._bind_scroll(self.legend)
        self._bind_scroll(self._legend_frame)
        self.legend.yview_moveto(0)
        self._legend_width()
        self._draw()

    def _color(self, key):
        return self._colors.get(key, "#64748b")

    def _bind_scroll(self, widget):
        widget.bind("<MouseWheel>", lambda event: self._scroll_legend(-1 if event.delta > 0 else 1))
        widget.bind("<Button-4>", lambda event: self._scroll_legend(-1))
        widget.bind("<Button-5>", lambda event: self._scroll_legend(1))

    def _scroll_legend(self, direction):
        if self.legend.yview() != (0.0, 1.0):
            self.legend.yview_scroll(direction, "units")
        return "break"

    def _legend_size(self, _event=None):
        content_height = self._legend_frame.winfo_reqheight() if self._data else 0
        viewport_height = self.legend.winfo_height()
        self.legend.coords(self._legend_window, 0, max(0, (viewport_height - content_height) / 2))
        self.legend.configure(scrollregion=(0, 0, self.legend.winfo_width(),
                                            max(content_height, viewport_height)))
        if content_height <= viewport_height:
            self.legend.yview_moveto(0)

    def _legend_width(self, _event=None):
        width = self.legend.winfo_width()
        self.legend.itemconfigure(self._legend_window, width=width)
        for widget in self._legend_frame.winfo_children():
            if int(widget.grid_info().get("column", -1)) == 1:
                widget.configure(wraplength=max(40, width - 90))
        self._legend_size()

    def _draw(self, _event=None):
        self.canvas.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not any(self._fractions.values()):
            self.canvas.create_text(width / 2, height / 2, text=self._empty_message,
                                    width=max(40, width - 24), fill=MUTED_TEXT, font="TkDefaultFont")
            return
        radius = max(1, (min(width, height) - 28) / 2)
        bounds = (width / 2 - radius, height / 2 - radius, width / 2 + radius, height / 2 + radius)
        positive = [(key, fraction) for key, fraction in self._fractions.items() if fraction > 0]
        if len(positive) == 1:
            self.canvas.create_oval(*bounds, fill=self._color(positive[0][0]), outline="")
            return
        start = 90.0
        for key, fraction in positive:
            extent = -360 * fraction
            self.canvas.create_arc(*bounds, start=start, extent=extent, fill=self._color(key),
                                   outline=self.canvas.cget("background"), width=1)
            start += extent
