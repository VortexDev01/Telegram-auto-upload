"""
Telegram Auto Uploader — Entry Point
A Desktop Telegram Bulk Media Management System.
"""

from ui.main_window import MainWindow


def main():
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
