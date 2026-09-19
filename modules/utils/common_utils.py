import os
import sys
import uuid


def new_object_id() -> str:
    """A stable, collision-safe identity for one editable logical object.

    Used as ``object_id`` on text items, text blocks, rectangles, brush strokes
    and inpaint patches so a logical object keeps the same identity across
    edit/move/undo/redo/save/load and webtoon page clipping — independent of its
    geometry, position, text or list index. UUID4 hex: no coordination needed
    between the pipeline, the canvas and the project file, all of which mint ids.
    """
    return uuid.uuid4().hex


def restart_application():
    """
    Restart the application.
    Works for both running as script and compiled executable (PyInstaller/Nuitka).
    """
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QProcess
    
    # Get the executable path (works for both script and compiled)
    if getattr(sys, 'frozen', False):
        # Running as compiled executable (PyInstaller/Nuitka)
        executable = sys.executable
        args = sys.argv[1:]  # Skip the executable name
    else:
        # Running as script
        executable = sys.executable
        args = sys.argv
    
    # Start new instance
    QProcess.startDetached(executable, args)
    
    # Quit current instance
    QApplication.quit()

def is_close(value1, value2, tolerance=2):
    return abs(value1 - value2) <= tolerance

def is_directory_empty(directory):
    # Walk through the directory
    for root, dirs, files in os.walk(directory):
        # If any file is found, the directory is not empty
        if files:
            return False
    # If no files are found, check if there are any subdirectories
    for root, dirs, files in os.walk(directory):
        if dirs:
            # Recursively check subdirectories
            for dir in dirs:
                if not is_directory_empty(os.path.join(root, dir)):
                    return False
    return True
