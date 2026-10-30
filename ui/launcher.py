"""Show a startup owner before loading the application or opening native dialogs."""

from datetime import datetime
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback

from portfolio import preferences
from ui.theme import configure_theme


def launch(directory, *, base_currency='EUR'):
    log = _StartupLog()
    root = None
    try:
        root = tk.Tk(className='PortfolioPlanner')
        root.title('Portfolio planner')
        root.geometry('540x160')
        configure_theme(root)
        startup = ttk.Frame(root, padding=24)
        startup.pack(fill='both', expand=True)
        status = ttk.Label(startup, text='Opening portfolio…', wraplength=480)
        status.pack(anchor='w', pady=16)
        root.update()
        log.write('Startup window visible')

        from ui.startup import select_config_directory

        log.write('Selecting configuration directory')
        directory = select_config_directory(
            directory, base_currency=base_currency,
            choose_directory=lambda: filedialog.askdirectory(
                title='Choose portfolio configuration directory', parent=root),
            show_error=lambda message: messagebox.showerror('Cannot open portfolio', message, parent=root))
        if directory is None:
            log.write('Selection cancelled')
            root.destroy()
            return 0
        status.configure(text='Loading portfolio planner…')
        root.update_idletasks()
        log.write('Loading application modules')
        from ui.app import PortfolioApp

        startup.destroy()
        log.write('Opening portfolio')
        PortfolioApp(root, directory, base_currency=base_currency, on_directory_opened=preferences.remember_directory)
        log.write('Application ready')
    except Exception as error:
        log.failure(error)
        _show_startup_error(root, error, log.path)
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        return 1
    root.mainloop()
    return 0


class _StartupLog:
    """Replace one small local log per launch; never record input values or paths."""

    def __init__(self):
        self.path = None
        try:
            path = preferences.preferences_path().with_name('startup.log')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('', encoding='utf-8')
            self.path = path
        except OSError:
            pass
        self.write('Starting portfolio planner')

    def write(self, message):
        if self.path is not None:
            try:
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(f'{datetime.now().isoformat(timespec="seconds")} {message}\n')
            except OSError:
                pass

    def failure(self, error):
        # Input errors may contain private values; import errors identify missing
        # modules/DLLs and are essential for diagnosing a portable installation.
        frames = traceback.extract_tb(error.__traceback__)
        location = ' -> '.join(f'{Path(frame.filename).name}:{frame.lineno} {frame.name}' for frame in frames)
        detail = f': {error}' if isinstance(error, ImportError) else ''
        self.write(f'Startup failed: {type(error).__name__}{detail}; {location}')


def _show_startup_error(root, error, log_path):
    message = f'Portfolio planner could not start.\n\n{error}'
    if log_path is not None:
        message += f'\n\nStartup details: {log_path}'
    try:
        messagebox.showerror('Cannot start portfolio planner', message, parent=root)
    except tk.TclError:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Cannot start portfolio planner', 0x10)
