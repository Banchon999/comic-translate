from .dayu_widgets.message import MMessage
from core.messages import content_flagged_text, server_error_text
from PySide6.QtCore import QCoreApplication, Qt
from PySide6 import QtWidgets

class Messages:

    @staticmethod
    def show_translation_complete(parent):

        MMessage.success(
            text=QCoreApplication.translate(
                "Messages", 
                "Comic has been Translated!"
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def select_font_error(parent):
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages", 
                "No Font selected.\nGo to Settings > Text Rendering > Font to select or import one "
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_not_logged_in_error(parent):
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages",
                "Please sign in or sign up via Settings > Account to continue."
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_translator_language_not_supported(parent):
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages",
                "The translator does not support the selected target language. Please choose a different language or tool."
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_missing_tool_error(parent, tool_name):
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages",
                "No {} selected. Please select a {} in Settings > Tools."
            ).format(tool_name, tool_name),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_insufficient_credits_error(parent, details: str = None):
        """
        Show an error message when the user has insufficient credits.
        
        Args:
            parent: parent widget
            details: optional detailed message from backend
        """
        msg = QtWidgets.QMessageBox(parent)
        msg.setIcon(QtWidgets.QMessageBox.Warning)
        msg.setWindowTitle(QCoreApplication.translate("Messages", "Insufficient Credits"))
        msg.setText(QCoreApplication.translate(
            "Messages", 
            "Insufficient credits to perform this action.\nGo to Settings > Account to buy more credits."
        ))
        
        if details:
            msg.setDetailedText(details)

        buy_btn = msg.addButton(
            QCoreApplication.translate("AccountPage", "Buy Credits"),
            QtWidgets.QMessageBox.ButtonRole.ActionRole,
        )
        ok_btn = msg.addButton(
            QCoreApplication.translate("Messages", "OK"),
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        msg.setDefaultButton(ok_btn)
        msg.exec()

        if msg.clickedButton() == buy_btn:
            settings_page = getattr(parent, "settings_page", None)
            if settings_page is not None and hasattr(settings_page, "start_buy_credits_flow"):
                settings_page.start_buy_credits_flow()

    @staticmethod
    def show_custom_not_configured_error(parent):
        """
        Show an error message when Custom is selected without proper configuration.
        Guides users to use the Credits system instead.
        """
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages",
                "Custom requires advanced API configuration. Most users should use the Credits system instead.\n"
                "Please sign in via Settings > Account to use credits, or configure Custom API settings in Settings > Advanced."
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_error_with_copy(parent, title: str, text: str, detailed_text: str | None = None):
        """
        Show a critical error dialog where the main text is selectable and the
        full details (traceback) are placed in the Details pane. A Copy button
        is provided to copy the full details to the clipboard.

        Args:
            parent: parent widget
            title: dialog window title
            text: short error text shown in the main area
            detailed_text: optional long text (traceback) shown in Details
        """
        msg = QtWidgets.QMessageBox(parent)
        msg.setIcon(QtWidgets.QMessageBox.Critical)
        msg.setWindowTitle(title)
        msg.setText(text)
        if detailed_text:
            msg.setDetailedText(detailed_text)

        # Allow selecting the main text
        try:
            msg.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        except Exception:
            pass

        copy_btn = msg.addButton(QCoreApplication.translate("Messages", "Copy"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton(QCoreApplication.translate("Messages", "OK"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(QCoreApplication.translate("Messages", "Close"), QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(ok_btn)
        msg.exec()

        if msg.clickedButton() == copy_btn:
            try:
                QtWidgets.QApplication.clipboard().setText(detailed_text or text)
            except Exception:
                pass

    @staticmethod
    def get_server_error_text(status_code: int = 500, context: str = None) -> str:
        """Localised text for a 5xx. Thread-safe — touches no UI.

        The implementation is in core.messages so the pipeline can build this
        text without importing the widget layer.
        """
        return server_error_text(status_code, context)

    @staticmethod
    def show_server_error(parent, status_code: int = 500, context: str = None):
        """
        Show a user-friendly error for 5xx server issues.
        
        Args:
            parent: parent widget
            status_code: HTTP status code
            context: optional context ('translation', 'ocr', or None for generic)
        """
        text = Messages.get_server_error_text(status_code, context)
        MMessage.error(
            text=text,
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_network_error(parent):
        """
        Show a user-friendly error for network/connectivity issues.
        """
        MMessage.error(
            text=QCoreApplication.translate(
                "Messages", 
                "Unable to connect to the server.\nPlease check your internet connection."
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def get_content_flagged_text(details: str = None, context: str = "Operation") -> str:
        """Build the standardized content-flagged error text."""
        return content_flagged_text(details=details, context=context)

    @staticmethod
    def show_content_flagged_error(parent, details: str = None, context: str = "Operation", duration=None, closable=True):
        """
        Show a friendly error when content is blocked by safety filters.
        """
        msg_text = Messages.get_content_flagged_text(details=details, context=context)
        return MMessage.error(
            text=msg_text,
            parent=parent,
            duration=duration,
            closable=closable
        )

    @staticmethod
    def show_nothing_to_clean(parent):
        """Clean was pressed with no area marked.

        Cleaning reads brush strokes, lasso and wand regions, and boxes the user
        has selected. With none of those the operation has nothing to do, and it
        used to return silently — which reads as a broken button rather than as
        a missing step.
        """
        return MMessage.info(
            text=QCoreApplication.translate(
                "Messages",
                "Nothing is marked to clean.\n"
                "Draw a box over the leftover text, or paint it with the brush, "
                "lasso or magic wand, then press Clean."
            ),
            parent=parent,
            duration=None,
            closable=True
        )

    @staticmethod
    def show_batch_skipped_summary(parent, skipped_count: int):
        """
        Show a persistent summary when a batch finished with skipped images.
        """
        text = QCoreApplication.translate(
            "Messages",
            "{0} image(s) were skipped in this batch.\nOpen Batch Report to see all skipped images and reasons."
        ).format(skipped_count)
        return MMessage.warning(
            text=text,
            parent=parent,
            duration=None,
            closable=True
        )

