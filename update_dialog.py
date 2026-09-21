"""
update_dialog.py - The dialog behind the header's "Update" button: what's new,
then one click to download, check, unpack and install the new version.

Downloading and unpacking (a ~300 MB bundle) run on a worker thread so the window
stays responsive and the download can be cancelled. Once the installer script has
been started -- detached, so it outlives this app -- `install_started` fires and
MainWindow quits; the script replaces the app's files and starts the new version.
If anything fails, the reason is shown here and the app carries on unchanged.
"""
import os
import shutil

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QProgressBar, QPushButton
)

import update_checker
from version import VERSION


class _InstallWorker(QThread):
    progress = pyqtSignal(int, int)  # bytes loaded, bytes total
    stage = pyqtSignal(str)
    ready = pyqtSignal(str)  # the unpacked folder that holds the install script
    failed = pyqtSignal(str)  # the reason; empty when the user cancelled

    def __init__(self, asset, parent=None):
        super().__init__(parent)
        self._asset = asset
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            work = update_checker.work_dir()
            shutil.rmtree(work, ignore_errors=True)
            zip_path = os.path.join(work, "bundle.zip")
            self.stage.emit("Downloading…")
            update_checker.download_asset(
                self._asset, zip_path,
                progress=lambda loaded, total: self.progress.emit(loaded, total),
                should_cancel=lambda: self._cancelled,
            )
            self.stage.emit("Unpacking…")
            unpacked = os.path.join(work, "unpacked")
            update_checker.extract_zip(zip_path, unpacked)
            os.remove(zip_path)  # ~300 MB nobody needs now
            self.ready.emit(update_checker.find_installer_dir(unpacked))
        except update_checker.UpdateCancelled:
            self.failed.emit("")
        except update_checker.UpdateError as e:
            self.failed.emit(str(e))
        except Exception as e:  # a worker thread must never let an exception escape
            self.failed.emit(f"unexpected error ({e})")


class UpdateDialog(QDialog):
    # The installer has been started; the app should quit now so it can replace its own files.
    install_started = pyqtSignal()

    def __init__(self, release, can_install, parent=None):
        super().__init__(parent)
        self.release = release
        self._worker = None
        self.setWindowTitle("Update Pawmodoro")
        self.resize(540, 440)

        layout = QVBoxLayout(self)
        title = QLabel(f"<b>Pawmodoro {release.version}</b> is available")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"You have v{VERSION}."))

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(release.notes or "No release notes.")
        layout.addWidget(self.notes, 1)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.update_btn = QPushButton("Update now")
        self.update_btn.setDefault(True)
        self.update_btn.clicked.connect(self._start)
        self.page_btn = QPushButton("Release page")
        self.page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page)))
        self.later_btn = QPushButton("Later")
        self.later_btn.clicked.connect(self.reject)
        for button in (self.update_btn, self.page_btn, self.later_btn):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        if release.asset is None:
            self.info.setText("This release has no installer package attached, so it can't be installed from here — open the release page to download it.")
        elif not can_install:
            self.info.setText("This copy of Pawmodoro runs from a source folder rather than an installed one, so it can't replace itself. "
                              "Update it with git pull, or install the release from its page.")
        self.update_btn.setVisible(release.asset is not None and can_install)

    # ---------- install flow ----------

    def _set_busy(self, busy):
        self.update_btn.setEnabled(not busy)
        self.page_btn.setEnabled(not busy)
        self.later_btn.setText("Cancel" if busy else "Later")
        self.bar.setVisible(busy)
        self.status.setVisible(busy or bool(self.status.text()))

    def _start(self):
        self.status.setStyleSheet("")
        self.status.setText("Starting…")
        self.bar.setValue(0)
        self._set_busy(True)
        self._worker = _InstallWorker(self.release.asset, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.stage.connect(self.status.setText)
        self._worker.ready.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, loaded, total):
        if total > 0:
            self.bar.setValue(int(loaded * 100 / total))
            self.status.setText(f"Downloading… {loaded / 1_048_576:.0f} of {total / 1_048_576:.0f} MB")

    def _on_ready(self, src_dir):
        self.bar.setValue(100)
        self.status.setText("Installing — Pawmodoro will restart in a moment…")
        try:
            update_checker.launch_installer(src_dir)
        except update_checker.UpdateError as e:
            self._on_failed(str(e))
            return
        self.install_started.emit()
        self.accept()

    def _on_failed(self, reason):
        self._set_busy(False)
        if not reason:  # cancelled
            self.status.setText("")
            self.status.setVisible(False)
            return
        self.status.setText(f"The update didn't work: {reason}. Nothing was changed. You can try again, or download it from the release page.")
        self.status.setStyleSheet("color: #b3261e;")
        self.status.setVisible(True)

    def reject(self):
        # While downloading, the button is "Cancel": stop the download, keep the dialog.
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            return
        super().reject()

    def done(self, result):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
        super().done(result)
