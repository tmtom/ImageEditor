from typing import Any, Dict, List, Optional

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:
    from .LibGPhotoCamera import (
        GP_WIDGET_BUTTON,
        GP_WIDGET_DATE,
        GP_WIDGET_MENU,
        GP_WIDGET_RADIO,
        GP_WIDGET_RANGE,
        GP_WIDGET_TEXT,
        GP_WIDGET_TOGGLE,
        get_default_camera_manager,
    )
except ImportError:
    from LibGPhotoCamera import (
        GP_WIDGET_BUTTON,
        GP_WIDGET_DATE,
        GP_WIDGET_MENU,
        GP_WIDGET_RADIO,
        GP_WIDGET_RANGE,
        GP_WIDGET_TEXT,
        GP_WIDGET_TOGGLE,
        get_default_camera_manager,
    )

try:
    from OpenNumismat.Tools.DialogDecorators import storeDlgSizeDecorator
except ModuleNotFoundError:
    from Tools.DialogDecorators import storeDlgSizeDecorator


# Match normalized libgphoto2 labels here. Add entries as cameras expose other
# settings that are not useful for image capture in this editor.
HIDDEN_CAMERA_SETTING_LABELS = frozenset({
    "ownername",
    "synchronizecameradateandtimewithpc",
    "dateandtime",
    "listallfiles",
})


@storeDlgSizeDecorator
class CameraGPhotoDialog(QDialog):
    """Dialog for choosing a libgphoto2 camera, configuring it, and capturing."""

    def __init__(
        self, parent: Optional[QWidget] = None, camera_manager: Optional[Any] = None
    ) -> None:
        """Build the camera UI and initialize camera discovery.

        Args:
            parent: Optional Qt parent widget.
            camera_manager: Optional shared camera manager; the process default
                manager is used when omitted.
        """
        super().__init__(parent)
        self.setWindowIcon(QIcon(":/webcam.png"))
        self.setWindowTitle(self.tr("Camera (libgphoto2)"))
        self.resize(440, 520)

        self.camera = camera_manager or get_default_camera_manager()
        app = QApplication.instance()
        if app is not None and not getattr(self.camera, "_qt_shutdown_connected", False):
            app.aboutToQuit.connect(self.camera.close)
            self.camera._qt_shutdown_connected = True
        self.image = None
        self._parameters = []
        self._controls = {}
        self._selected = None
        self._changing_camera = False
        self._standard_capture_available = False
        self._preview_available = False
        self._cached_config = False

        self.cameraSelector = QComboBox()
        self.cameraSelector.currentIndexChanged.connect(self.cameraSelectionChanged)
        self.statusLabel = QLabel()
        self.statusLabel.setWordWrap(True)
        self.previewCheckbox = QCheckBox(self.tr("Capture in preview mode"))
        self.previewCheckbox.toggled.connect(self.previewChanged)
        self.restoreTargetCheckbox = QCheckBox(
            self.tr("Restore original capture target after each shot")
        )
        self.restoreTargetCheckbox.setChecked(True)
        self.restoreTargetCheckbox.toggled.connect(self._configChanged)

        self.reevaluateButton = QPushButton(self.tr("Re-evaluate cameras"))
        self.reevaluateButton.clicked.connect(self.reevaluateCameras)

        self.configWidget = QWidget()
        self.configForm = QFormLayout(self.configWidget)
        self.configForm.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.configScroll = QScrollArea()
        self.configScroll.setWidgetResizable(True)
        self.configScroll.setWidget(self.configWidget)

        self.captureButton = QPushButton(self.tr("Shoot"))
        self.captureButton.setEnabled(False)
        self.captureButton.clicked.connect(self.capture)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.captureButton)
        layout = QVBoxLayout(self)
        layout.addWidget(self.reevaluateButton)
        layout.addWidget(self.cameraSelector)
        layout.addWidget(self.previewCheckbox)
        layout.addWidget(self.restoreTargetCheckbox)
        layout.addWidget(self.configScroll, 1)
        layout.addWidget(self.statusLabel)
        layout.addLayout(button_layout)

        self.initializeCameras()

    def initializeCameras(self) -> None:
        """Populate the selector and load the last-used detected camera."""
        try:
            cameras = self.camera.cameras()
        except (OSError, RuntimeError, AttributeError) as error:
            self.cameraSelector.setEnabled(False)
            self.previewCheckbox.setEnabled(False)
            self.statusLabel.setText(self.tr("Could not initialize libgphoto2."))
            QMessageBox.warning(self, self.tr("Camera Error"), str(error))
            return

        self.cameraSelector.blockSignals(True)
        for camera in cameras:
            display = f"{camera['model']} ({camera['port']})" if camera["port"] else camera["model"]
            self.cameraSelector.addItem(display, camera)
        self.cameraSelector.blockSignals(False)

        if not cameras:
            self.cameraSelector.setEnabled(False)
            self.previewCheckbox.setEnabled(False)
            self.restoreTargetCheckbox.setEnabled(False)
            self.statusLabel.setText(self.tr("No cameras detected."))
            return

        selected_index = 0
        last_camera = self.camera.last_camera
        if last_camera:
            for index, camera in enumerate(cameras):
                if self.camera.camera_key(camera) == self.camera.camera_key(last_camera):
                    selected_index = index
                    break
        self.cameraSelector.setCurrentIndex(selected_index)
        self.cameraSelectionChanged(selected_index)

    def _save_current_values(self) -> None:
        """Read editable controls and persist their values for this camera."""
        if self._selected is None:
            return
        values = {}
        for path, (widget, kind, parameter) in self._controls.items():
            if parameter["readonly"]:
                continue
            if kind == GP_WIDGET_TOGGLE:
                value = int(widget.isChecked())
            elif kind in (GP_WIDGET_RADIO, GP_WIDGET_MENU):
                value = widget.currentData()
            elif kind == GP_WIDGET_TEXT:
                value = widget.text()
            else:
                value = widget.value()
            values[path] = value
        self.camera.save_preferences(
            self._selected, values, self.previewCheckbox.isChecked(),
            self.restoreTargetCheckbox.isChecked(),
        )

    def cameraSelectionChanged(self, index: int) -> None:
        """Load a selected camera's schema and restore valid saved values."""
        if self._changing_camera or index < 0 or self.camera is None:
            return
        self._save_current_values()
        selected = self.cameraSelector.itemData(index)
        self._selected = selected
        self.captureButton.setEnabled(False)
        self.statusLabel.setText(self.tr("Reading camera settings..."))
        self.statusLabel.repaint()
        try:
            info = self.camera.inspect(selected)
        except (OSError, RuntimeError, AttributeError) as error:
            self._clear_controls()
            self.previewCheckbox.setEnabled(False)
            self.statusLabel.setText(self.tr("Could not read camera settings."))
            QMessageBox.warning(self, self.tr("Camera Error"), str(error))
            return

        self._parameters = info["parameters"]
        self._cached_config = info.pop("_cached", False)
        preferences = self.camera.preferences(selected)
        remembered = preferences.get("settings", {})
        # Retain only values still supported by this camera's config schema.
        supported = {item["path"]: item for item in self._parameters}
        remembered = {
            path: value for path, value in remembered.items()
            if path in supported and not supported[path]["readonly"]
            and (not supported[path]["choices"] or str(value) in supported[path]["choices"])
        }
        self._build_controls(self._parameters, remembered)
        preview_available = info["preview"]
        self._preview_available = preview_available
        self._standard_capture_available = info["capture"]
        self.previewCheckbox.setEnabled(preview_available)
        self.previewCheckbox.blockSignals(True)
        preview_default = preferences.get("preview", preview_available)
        if preview_available and not self._standard_capture_available:
            preview_default = True
        self.previewCheckbox.setChecked(preview_default)
        self.previewCheckbox.blockSignals(False)
        self.restoreTargetCheckbox.blockSignals(True)
        self.restoreTargetCheckbox.setChecked(preferences.get("restore_target", True))
        self.restoreTargetCheckbox.blockSignals(False)

        capture_available = self._standard_capture_available or preview_available
        self._update_capture_availability()
        if self._cached_config:
            self.statusLabel.setText(
                self.tr("Camera is unavailable; showing saved settings. Capture is disabled.")
            )
        elif not capture_available:
            self.statusLabel.setText(self.tr("This camera does not advertise image capture."))
        else:
            self.statusLabel.setText(self.tr("Camera settings loaded."))
        self._save_current_values()

    def _standard_ready(self) -> bool:
        """Return whether standard capture currently targets internal RAM."""
        if not self._standard_capture_available:
            return False
        target_control = next(
            ((widget, parameter) for path, (widget, kind, parameter) in self._controls.items()
             if path.endswith("/capturetarget") and kind in (GP_WIDGET_RADIO, GP_WIDGET_MENU)),
            None,
        )
        if target_control:
            return self._is_ram_choice(target_control[0].currentData())
        target_parameter = next(
            (parameter for parameter in self._parameters
             if parameter["path"].endswith("/capturetarget")),
            None,
        )
        return bool(target_parameter and self._is_ram_choice(target_parameter["value"]))

    def _update_capture_availability(self) -> None:
        """Update the capture button and any RAM-safety status message."""
        available = not self._cached_config and (
            self._preview_available if self.previewCheckbox.isChecked()
            else self._standard_ready()
        )
        self.captureButton.setEnabled(available)
        if self._cached_config:
            self.statusLabel.setText(
                self.tr("Camera is unavailable; showing saved settings. Capture is disabled.")
            )
            return
        if not available and not self.previewCheckbox.isChecked():
            self.statusLabel.setText(
                self.tr("Standard capture is unavailable unless an internal-RAM target is selected.")
            )

    def _configChanged(self, *_args: Any) -> None:
        """Persist changed controls and recalculate capture availability."""
        if self._changing_camera:
            return
        self._save_current_values()
        self._update_capture_availability()

    def _clear_controls(self) -> None:
        """Remove all generated setting rows from the form."""
        while self.configForm.rowCount():
            self.configForm.removeRow(0)
        self._controls.clear()

    def _build_controls(
        self,
        parameters: List[Dict[str, Any]],
        remembered: Dict[str, Any],
    ) -> None:
        """Create controls for supported settings, using remembered values."""
        self._changing_camera = True
        self._clear_controls()
        self._controls = {}
        for parameter in parameters:
            if self._is_hidden_parameter(parameter):
                continue
            kind = parameter["type"]
            if kind == GP_WIDGET_BUTTON:
                continue
            path = parameter["path"]
            value = remembered.get(path, parameter["value"])
            if path.endswith("/capturesizeclass") and path not in remembered:
                full_image = next(
                    (choice for choice in parameter["choices"]
                     if choice.strip().lower() == "full image"),
                    None,
                )
                if full_image is not None:
                    value = full_image
            if path.endswith("/capturetarget") and path not in remembered:
                internal = next(
                    (choice for choice in parameter["choices"]
                     if self._is_ram_choice(choice)),
                    None,
                )
                if internal is not None:
                    value = internal

            widget = self._make_control(parameter, value)
            if widget is None:
                continue
            widget.setEnabled(not parameter["readonly"])
            widget.setToolTip(path)
            label = self._translate_camera_text(parameter["label"], path, "label")
            self.configForm.addRow(label, widget)
            self._controls[path] = (widget, kind, parameter)
            signal = getattr(widget, "currentIndexChanged", None)
            if signal is not None:
                signal.connect(self._configChanged)
            elif kind == GP_WIDGET_TOGGLE:
                widget.toggled.connect(self._configChanged)
            elif kind == GP_WIDGET_TEXT:
                widget.editingFinished.connect(self._configChanged)
            else:
                widget.valueChanged.connect(self._configChanged)
        self._changing_camera = False

    @staticmethod
    def _is_ram_choice(choice: str) -> bool:
        """Recognize an internal-memory target while rejecting card targets."""
        normalized = choice.lower().strip()
        return "card" not in normalized and any(word in normalized for word in ("ram", "sdram", "internal"))

    @staticmethod
    def _normalize_camera_text(text: Any) -> str:
        """Normalize a setting label for comparison with the hidden-label list."""
        return "".join(character for character in str(text).casefold() if character.isalnum())

    @classmethod
    def _is_hidden_parameter(
        cls: type, parameter: Dict[str, Any]
    ) -> bool:
        """Return whether a config parameter is intentionally hidden."""
        return cls._normalize_camera_text(parameter.get("label", "")) in HIDDEN_CAMERA_SETTING_LABELS

    @staticmethod
    def _translate_camera_text(text: str, path: str, role: str) -> str:
        """Translate dynamic camera text, falling back to libgphoto2's wording.

        The Qt disambiguation (`label:/config/path` or `choice:/config/path`)
        lets catalogs distinguish identical words used by different settings.
        A no-disambiguation lookup also supports generic translations.
        """
        translated = QCoreApplication.translate(
            "CameraSettings", text, f"{role}:{path}"
        )
        if translated == text:
            translated = QCoreApplication.translate("CameraSettings", text)
        return translated

    def _make_control(
        self, parameter: Dict[str, Any], value: Any
    ) -> Optional[QWidget]:
        """Build the Qt editor matching a libgphoto2 widget type."""
        kind = parameter["type"]
        if kind in (GP_WIDGET_RADIO, GP_WIDGET_MENU):
            combo = QComboBox()
            for choice in parameter["choices"]:
                combo.addItem(
                    self._translate_camera_text(choice, parameter["path"], "choice"),
                    choice,
                )
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
            return combo
        if kind == GP_WIDGET_TOGGLE:
            checkbox = QCheckBox()
            checkbox.setChecked(bool(value))
            return checkbox
        if kind == GP_WIDGET_TEXT:
            edit = QLineEdit("" if value is None else str(value))
            return edit
        if kind == GP_WIDGET_RANGE:
            low, high, step = parameter.get("range", (0.0, 100.0, 1.0))
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setSingleStep(step if step > 0 else 1.0)
            spin.setDecimals(4 if step < 1 else 2)
            if value is not None:
                spin.setValue(value)
            return spin
        if kind == GP_WIDGET_DATE:
            spin = QSpinBox()
            spin.setRange(-2147483647, 2147483647)
            if value is not None:
                spin.setValue(value)
            return spin
        return None

    def previewChanged(self, _checked: bool) -> None:
        """Persist preview mode and update capture availability."""
        self._save_current_values()
        self._update_capture_availability()

    def reject(self) -> None:
        """Save current preferences before the dialog is dismissed."""
        self._save_current_values()
        super().reject()

    def capture(self) -> None:
        """Capture the selected image and accept the dialog on success."""
        if self._selected is None:
            return
        self._save_current_values()
        settings = self.camera.preferences(self._selected).get("settings", {})
        preview = self.previewCheckbox.isChecked() and self.previewCheckbox.isEnabled()
        restore_target = self.restoreTargetCheckbox.isChecked()
        self.captureButton.setEnabled(False)
        self.cameraSelector.setEnabled(False)
        self.statusLabel.setText(self.tr("Capturing preview..." if preview else "Capturing image..."))
        self.statusLabel.repaint()
        try:
            image_data = self.camera.capture(
                self._selected, settings, preview=preview,
                restore_target=restore_target,
            )
            image = QImage.fromData(image_data)
            if image.isNull():
                raise RuntimeError(
                    self.tr("The captured data is not a supported image")
                )
            self.image = image
        except (OSError, RuntimeError, AttributeError, ValueError) as error:
            self.statusLabel.setText(self.tr("Capture failed."))
            QMessageBox.warning(self, self.tr("Camera Error"), str(error))
        else:
            self.accept()
        finally:
            self.cameraSelector.setEnabled(True)
            self._update_capture_availability()

    def reevaluateCameras(self) -> None:
        """Restore changed settings, rediscover cameras, and rebuild selection."""
        self._save_current_values()
        self.captureButton.setEnabled(False)
        self.statusLabel.setText(self.tr("Re-evaluating cameras..."))
        self.statusLabel.repaint()
        try:
            cameras = self.camera.reevaluate()
        except Exception as error:
            self.cameraSelector.setEnabled(False)
            self.statusLabel.setText(self.tr("Could not re-evaluate cameras."))
            QMessageBox.warning(self, self.tr("Camera Error"), str(error))
            return

        # Autodetection can still report a USB camera that is powered off or
        # otherwise unreachable. Only keep candidates that return a live schema.
        available_cameras = []
        for camera in cameras:
            try:
                self.camera.inspect(camera, refresh=True)
            except Exception:
                continue
            available_cameras.append(camera)
        cameras = available_cameras

        last = self.camera.last_camera
        self._changing_camera = True
        self.cameraSelector.blockSignals(True)
        self.cameraSelector.clear()
        for camera in cameras:
            display = f"{camera['model']} ({camera['port']})" if camera["port"] else camera["model"]
            self.cameraSelector.addItem(display, camera)
        self.cameraSelector.blockSignals(False)
        self._changing_camera = False
        if not cameras:
            self._clear_controls()
            self._parameters = []
            self._selected = None
            self._cached_config = False
            self._preview_available = False
            self._standard_capture_available = False
            self.cameraSelector.setEnabled(False)
            self.previewCheckbox.setEnabled(False)
            self.restoreTargetCheckbox.setEnabled(False)
            self.statusLabel.setText(self.tr("No cameras detected."))
            return
        self.cameraSelector.setEnabled(True)
        self.restoreTargetCheckbox.setEnabled(True)
        index = next((
            i for i, camera in enumerate(cameras)
            if last and self.camera.camera_key(camera) == self.camera.camera_key(last)
        ), 0)
        self.cameraSelector.setCurrentIndex(index)
        self.cameraSelectionChanged(index)
