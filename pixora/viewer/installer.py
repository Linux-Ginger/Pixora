import sys
import subprocess
import threading
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
import os

DEPENDENCIES = [
    ("PyQt6", "PyQt6"),
    ("Pillow", "Pillow"),
    ("imagehash", "imagehash"),
]

class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def run(self):
        total = len(DEPENDENCIES)
        for i, (name, package) in enumerate(DEPENDENCIES):
            self.progress.emit(
                int((i / total) * 100),
                f"{name} installeren..."
            )
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", package, "-q"],
                    check=True
                )
            except subprocess.CalledProcessError:
                self.error.emit(f"Fout bij installeren van {name}")
                return

        self.progress.emit(100, "Installatie voltooid!")
        self.finished.emit()


class InstallerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixora Installer")
        self.setFixedSize(512, 650)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setup_ui()
        self.start_install()

    def setup_ui(self):
        self.setStyleSheet("""
            QWidget#main {
                background-color: #0f0a1e;
                border-radius: 18px;
            }
            QLabel#status {
                color: #94a3b8;
                font-size: 13px;
            }
            QProgressBar {
                background-color: #1e1a2e;
                border-radius: 8px;
                height: 16px;
                text-align: center;
                color: white;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #a855f7,
                    stop:0.3 #3b82f6,
                    stop:0.6 #22c55e,
                    stop:0.8 #f97316,
                    stop:1 #ef4444
                );
                border-radius: 8px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        main = QWidget()
        main.setObjectName("main")
        main.setFixedSize(512, 650)
        outer.addWidget(main)

        layout = QVBoxLayout(main)
        layout.setContentsMargins(50, 60, 50, 50)
        layout.setSpacing(0)

        # Logo afbeelding
        logo_label = QLabel()
        logo_path = os.path.join(os.path.dirname(__file__), "..", "docs", "pixora-logo.svg")
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            pixmap = pixmap.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(pixmap)
        else:
            # Fallback tekst als SVG niet laadt
            logo_label.setText("Pixora")
            logo_label.setFont(QFont("Georgia", 48, QFont.Weight.Bold))
            logo_label.setStyleSheet("color: #f1f5f9;")

        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label)

        layout.addSpacing(20)

        # Subtitel
        sub = QLabel("by LinuxGinger")
        sub.setFont(QFont("Georgia", 13))
        sub.setStyleSheet("color: #a855f7; letter-spacing: 2px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)

        layout.addStretch()

        # Status tekst
        self.status_label = QLabel("Voorbereiden...")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.status_label)

        layout.addSpacing(12)

        # Voortgangsbalk
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        layout.addSpacing(20)

        # Versie label
        version = QLabel("v0.1.0")
        version.setStyleSheet("color: #334155; font-size: 11px;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

    def start_install(self):
        self.worker = InstallWorker()
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def update_progress(self, value, message):
        self.progress_bar.setValue(value)
        self.status_label.setText(message)

    def on_finished(self):
        self.status_label.setText("Klaar! Pixora wordt gestart...")
        self.progress_bar.setValue(100)
        # Start setup wizard na korte pauze
        QThread.msleep(1500)
        self.close()
        from setup_wizard import SetupWizard
        self.wizard = SetupWizard()
        self.wizard.show()

    def on_error(self, message):
        self.status_label.setText(f"Fout: {message}")
        self.status_label.setStyleSheet("color: #ef4444; font-size: 13px;")


def main():
    app = QApplication(sys.argv)
    window = InstallerWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()