import os
import platform
import logging
import requests
import subprocess
import tempfile
import threading
from packaging import version
from PySide6.QtCore import QObject, Signal, QStandardPaths
from app.version import __version__

logger = logging.getLogger(__name__)

#: Files the update can run as they are.
INSTALLER_SUFFIXES = {
    "Windows": (".exe", ".msi"),
    "Darwin": (".dmg", ".pkg"),
}
#: Release archives (what release.yml publishes), per OS: a name fragment and
#: a suffix. An archive is downloaded and shown in its folder, never run.
ARCHIVES = {
    "Windows": ("windows", ".zip"),
    "Linux": ("linux", ".tar.gz"),
}


def choose_asset(assets: list[dict], system: str) -> str | None:
    """The download URL for this OS from a release's assets, or None.

    An installer wins when the release has one; otherwise the archive built
    for this OS (``ToonStudio-Windows-x86_64.zip``, ``ToonStudio-Linux-…``).
    """
    installers = INSTALLER_SUFFIXES.get(system, ())
    for asset in assets:
        if asset.get("name", "").lower().endswith(installers) and installers:
            return asset.get("browser_download_url")
    fragment, suffix = ARCHIVES.get(system, (None, None))
    if fragment:
        for asset in assets:
            name = asset.get("name", "").lower()
            if fragment in name and name.endswith(suffix):
                return asset.get("browser_download_url")
    return None


def is_installer(path: str, system: str | None = None) -> bool:
    """Whether a downloaded update is run (an installer) or shown (an archive)."""
    suffixes = INSTALLER_SUFFIXES.get(system or platform.system(), ())
    return bool(suffixes) and path.lower().endswith(suffixes)


class UpdateChecker(QObject):
    """
    Checks for updates on GitHub and handles downloading/running installers.

    The network work runs on a plain Python thread that only *emits this
    object's signals*; Qt queues each emission to the receivers on the GUI
    thread. No QObject is created, moved or destroyed on that thread. The
    previous version moved a Python QObject worker onto a QThread and deleted
    it there with deleteLater — destroying a Python-derived QObject needs the
    GIL while Qt holds a pooled signal/slot mutex, so a check finishing while
    the GUI thread was making connections could deadlock the process for good
    (tests/test_update_checker.py has the full story).
    """
    update_available = Signal(str, str, str)  # version, release_notes, download_url
    up_to_date = Signal()
    error_occurred = Signal(str)
    download_progress = Signal(int)
    download_finished = Signal(str) # file_path

    # Toon Studio's own releases (release.yml publishes them); asking the
    # upstream project offered its versions as updates to this app.
    REPO_OWNER = "Banchon999"
    REPO_NAME = "comic-translate"

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None

    def _start(self, work) -> None:
        # Daemon: a request stuck on a dead network must not keep the app
        # from exiting. The previous request, if any, is left to finish on
        # its own; its signals still arrive, which is what they always did.
        self._thread = threading.Thread(target=work, name="update-checker", daemon=True)
        self._thread.start()

    def _emit(self, signal_name: str, *args) -> None:
        """Emit one of this object's signals from the worker thread.

        The window may have closed, and this object been deleted, while the
        request was out; a late result then has nowhere to go and is dropped.
        """
        try:
            getattr(self, signal_name).emit(*args)
        except RuntimeError:
            logger.debug("Update checker gone before its %s result arrived", signal_name)

    def check_for_updates(self):
        """Starts the check in a background thread."""
        worker = UpdateWorker(self.REPO_OWNER, self.REPO_NAME, __version__, self._emit)
        self._start(worker.run)

    def download_installer(self, url, filename):
        """Starts the download in a background thread."""
        worker = DownloadWorker(url, filename, self._emit)
        self._start(worker.run)

    def run_installer(self, file_path):
        """Run a downloaded installer, or show a downloaded archive in its
        folder — an archive is unpacked by the user, never executed."""
        try:
            system = platform.system()
            if not is_installer(file_path, system):
                self.reveal(file_path, system)
                return
            if system == "Windows":
                # Use os.startfile; Windows will parse the installer manifest
                # and trigger UAC only if the installer requires it.
                os.startfile(file_path)
            elif system == "Darwin": # macOS
                subprocess.Popen(["open", file_path])
        except Exception as e:
            self.error_occurred.emit(f"Failed to launch installer: {e}")

    @staticmethod
    def reveal(file_path, system=None):
        """Open the file manager at `file_path`, selected where the OS can."""
        system = system or platform.system()
        if system == "Windows":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", file_path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(os.path.abspath(file_path))])

    def shutdown(self, timeout: float = 1.0):
        """Wait briefly for a running request (best-effort).

        The thread is a daemon and touches no Qt object, so one still waiting
        on the network after this is harmless: its late result is dropped.
        """
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and timeout:
            thread.join(timeout)


class UpdateWorker:
    """Fetches the latest release and reports through ``emit(signal, *args)``
    — the checker's signals. Deliberately not a QObject (see UpdateChecker)."""

    def __init__(self, owner, repo, current_version, emit):
        self.owner = owner
        self.repo = repo
        self.current_version = current_version
        self.emit = emit

    def run(self):
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}/releases/latest"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            latest_tag = data.get("tag_name", "").lstrip("v")
            if not latest_tag:
                 self.emit("error_occurred", "Could not parse version from release.")
                 return

            if version.parse(latest_tag) > version.parse(self.current_version):
                asset_url = choose_asset(data.get("assets", []), platform.system())
                if asset_url:
                    self.emit("update_available", latest_tag, data.get("html_url", ""), asset_url)
                else:
                    self.emit("error_occurred", f"New version {latest_tag} available, but no installer found for your OS.")
            else:
                self.emit("up_to_date")

        except Exception as e:
            self.emit("error_occurred", str(e))


class DownloadWorker:
    """Downloads an installer and reports through ``emit(signal, *args)`` —
    the checker's signals. Deliberately not a QObject (see UpdateChecker)."""

    def __init__(self, url, filename, emit):
        self.url = url
        self.filename = filename
        self.emit = emit

    def run(self):
        try:
            # Download to Downloads directory
            download_dir = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            if not download_dir:
                download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            
            # Fallback to temp if Downloads doesn't exist
            if not os.path.exists(download_dir):
                download_dir = tempfile.gettempdir()

            save_path = os.path.join(download_dir, self.filename)
            
            response = requests.get(self.url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded_size / total_size) * 100)
                            self.emit("download_progress", percent)
            
            self.emit("download_finished", save_path)
            
        except Exception as e:
            self.emit("error_occurred", str(e))
