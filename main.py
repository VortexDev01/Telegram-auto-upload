"""
Telegram Auto Uploader — Entry Point
A Desktop Telegram Bulk Media Management System.
"""

import os

from loguru import logger

import database
from ui.main_window import MainWindow


def main():
    # Keep a log file next to the settings database for troubleshooting.
    logger.add(
        os.path.join(database.LOG_DIR, "autoupload.log"),
        rotation="5 MB", retention=5, encoding="utf-8", level="INFO",
    )
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
