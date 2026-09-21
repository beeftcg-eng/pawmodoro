"""
sync_settings_dialog.py - "Cloud Sync" settings dialog: lets you point the
desktop app at a Supabase project and log in, so notes/checklist/XP/quests
stay in sync with the phone web app. Reachable from View -> Cloud Sync...

The shared project from cloud_defaults.py is used unless the person opts
into "Use a different Supabase project", so friends only enter an email
and password. A config already saved for some other project is kept and
shown as-is.

First connect policy (see _reconcile): if the cloud side is empty, this
desktop's current local progress is uploaded to seed it. If the cloud side
already has data, it's pulled down and replaces the local copy (the cloud
is assumed to be the more current copy in that case). Either way this is
a one-time thing at connect time, not a repeated merge.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QLabel,
    QHBoxLayout, QMessageBox, QCheckBox, QWidget
)
from PyQt6.QtCore import Qt

from cloud_defaults import DEFAULT_URL, DEFAULT_ANON_KEY
from supabase_sync import SupabaseSync, SyncError


class SyncSettingsDialog(QDialog):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Cloud Sync")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Sync notes, checklist, shared list, and quest/XP progress with the phone web app.\n"
            "New here? Enter an email and password and press “Create account”. "
            "Already have one (on your phone, say)? Use the same details and press “Log in”."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        cfg = self.storage.get_sync_config()
        self.url_edit = QLineEdit(cfg.get("url") or DEFAULT_URL)
        self.url_edit.setPlaceholderText("https://xxxx.supabase.co")
        self.key_edit = QLineEdit(cfg.get("anon_key") or DEFAULT_ANON_KEY)
        self.key_edit.setPlaceholderText("anon public key")
        self.email_edit = QLineEdit(cfg.get("email", ""))
        self.email_edit.setPlaceholderText("you@example.com")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(
            "(already saved)" if cfg.get("enabled") else "password"
        )

        form.addRow("Email:", self.email_edit)
        form.addRow("Password:", self.password_edit)
        layout.addLayout(form)

        # The project fields are tucked away unless this install already
        # points somewhere other than the shared project.
        custom = (self.url_edit.text(), self.key_edit.text()) != (DEFAULT_URL, DEFAULT_ANON_KEY)
        self.custom_check = QCheckBox("Use a different Supabase project")
        self.custom_check.setChecked(custom)
        layout.addWidget(self.custom_check)
        self.project_box = QWidget()
        project_form = QFormLayout(self.project_box)
        project_form.setContentsMargins(0, 0, 0, 0)
        project_form.addRow("Project URL:", self.url_edit)
        project_form.addRow("Anon key:", self.key_edit)
        self.project_box.setVisible(custom)
        self.custom_check.toggled.connect(self._toggle_custom)
        layout.addWidget(self.project_box)

        engine = self.storage.sync
        self.status_label = QLabel(
            f"{'✅' if engine.status == 'online' else '⚠️'} {engine.status_text()}"
            if cfg.get("enabled") else "Not connected."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        login_btn = QPushButton("Log in")
        login_btn.clicked.connect(lambda: self._connect(sign_up=False))
        btn_row.addWidget(login_btn)

        signup_btn = QPushButton("Create account (first time)")
        signup_btn.clicked.connect(lambda: self._connect(sign_up=True))
        btn_row.addWidget(signup_btn)
        layout.addLayout(btn_row)

        disable_btn = QPushButton("Disconnect")
        disable_btn.clicked.connect(self._disconnect)
        layout.addWidget(disable_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _toggle_custom(self, on):
        self.project_box.setVisible(on)
        if not on:
            self.url_edit.setText(DEFAULT_URL)
            self.key_edit.setText(DEFAULT_ANON_KEY)
        self.adjustSize()

    def _connect(self, sign_up):
        url = self.url_edit.text().strip()
        anon_key = self.key_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text()
        if not url or not anon_key or not email or not password:
            self.status_label.setText("Email and password are required (plus the URL and key, for a custom project).")
            return

        client = SupabaseSync(url, anon_key)
        try:
            if sign_up:
                result = client.sign_up(email, password)
                if not client.logged_in:
                    self.status_label.setText(
                        (result or {}).get("msg")
                        or "Account created — check your email to confirm it, then Log in."
                    )
                    return
            else:
                client.login(email, password)
        except SyncError as e:
            self.status_label.setText(f"Couldn't connect: {e}")
            return

        try:
            self._reconcile(client)
        except SyncError as e:
            self.status_label.setText(f"Connected, but the first sync failed: {e}")
            return

        self.storage.save_sync_config(url, anon_key, email, client.refresh_token, enabled=True)
        self.status_label.setText("✅ Connected and syncing.")
        self.password_edit.clear()
        self.password_edit.setPlaceholderText("(already saved)")

    def _reconcile(self, client):
        """First-connect policy: adopt the cloud if it already has data,
        otherwise push this desktop's current local state up to seed it."""
        remote = client.sync_pull()
        remote_is_empty = (
            remote["xp"] == 0 and remote["total_pomodoros"] == 0
            and remote["total_tasks"] == 0 and not remote["checklist"]
            and not remote["notes"]
        )
        if remote_is_empty:
            g = self.storage.get_gamification()
            client.import_state(
                self.storage.get_notes(), g["xp"], g["total_pomodoros"], g["total_tasks"],
                g["current_streak"], g["longest_streak"], g.get("last_active_date"),
            )
            for task in self.storage.get_checklist():
                if task["recurrence"] == "weekday":
                    # "specific day" tasks are desktop-only: the cloud's
                    # recurrence constraint rejects them, which would fail
                    # this whole first sync.
                    continue
                client.add_task(
                    task["text"], task["recurrence"], task.get("reminder_time"), task.get("source", "checklist"),
                    task["id"],
                )
            QMessageBox.information(
                self, "Cloud Sync",
                "No existing cloud data found — uploaded this computer's current "
                "notes, checklist, and progress to seed it."
            )
        else:
            self.storage.adopt_remote_state(remote)
            QMessageBox.information(
                self, "Cloud Sync",
                "Found existing cloud data — replaced this computer's notes, "
                "checklist, and progress with the synced version."
            )

    def _disconnect(self):
        cfg = self.storage.get_sync_config()
        self.storage.save_sync_config(
            cfg.get("url", ""), cfg.get("anon_key", ""), cfg.get("email", ""),
            None, enabled=False,
        )
        self.status_label.setText("Not connected.")
