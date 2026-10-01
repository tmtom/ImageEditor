"""Small, UI-independent wrapper around libgphoto2's C API."""

import ctypes
import copy
import atexit
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple


GP_WIDGET_WINDOW = 0
GP_WIDGET_SECTION = 1
GP_WIDGET_TEXT = 2
GP_WIDGET_RANGE = 3
GP_WIDGET_TOGGLE = 4
GP_WIDGET_RADIO = 5
GP_WIDGET_MENU = 6
GP_WIDGET_BUTTON = 7
GP_WIDGET_DATE = 8
GP_OPERATION_CAPTURE_IMAGE = 1
GP_OPERATION_CAPTURE_PREVIEW = 8
GP_FILE_TYPE_NORMAL = 0

_dll_directory_handles = []
_msvcrt_runtime = None


class CameraAbilities(ctypes.Structure):
    """C-compatible libgphoto2 camera-driver capabilities structure."""

    _fields_ = [
        ("model", ctypes.c_char * 128), ("status", ctypes.c_int),
        ("port", ctypes.c_int), ("speed", ctypes.c_int * 64),
        ("operations", ctypes.c_int), ("file_operations", ctypes.c_int),
        ("folder_operations", ctypes.c_int), ("usb_vendor", ctypes.c_int),
        ("usb_product", ctypes.c_int), ("usb_class", ctypes.c_int),
        ("usb_subclass", ctypes.c_int), ("usb_protocol", ctypes.c_int),
        ("library", ctypes.c_char * 1024), ("id", ctypes.c_char * 1024),
        ("device_type", ctypes.c_int), ("reserved2", ctypes.c_int),
        ("reserved3", ctypes.c_int), ("reserved4", ctypes.c_int),
        ("reserved5", ctypes.c_int), ("reserved6", ctypes.c_int),
        ("reserved7", ctypes.c_int), ("reserved8", ctypes.c_int),
    ]


class CameraFilePath(ctypes.Structure):
    """C-compatible path returned by libgphoto2 after a capture."""

    _fields_ = [("name", ctypes.c_char * 128), ("folder", ctypes.c_char * 1024)]


def _msys2_paths() -> Tuple[str, str, str]:
    """Return the MSYS2 binary, camera-driver, and port-driver directories."""
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        raise OSError(f"MSYS2 bin folder not found at {msys2_bin}")

    def versioned_directory(path: str) -> str:
        """Choose the newest numeric-version subdirectory under ``path``."""
        versions = [
            name for name in os.listdir(path)
            if os.path.isdir(os.path.join(path, name))
            and all(part.isdigit() for part in name.split("."))
        ]
        if not versions:
            raise OSError(f"No libgphoto2 plugin directory found in {path}")
        versions.sort(key=lambda item: tuple(int(part) for part in item.split(".")))
        return os.path.join(path, versions[-1])

    msys2_lib = os.path.join(os.path.dirname(msys2_bin), "lib")
    return (
        msys2_bin,
        versioned_directory(os.path.join(msys2_lib, "libgphoto2")),
        versioned_directory(os.path.join(msys2_lib, "libgphoto2_port")),
    )


def _set_msys2_environment(name: str, value: str) -> None:
    """Set a process environment variable for the MSYS2 C runtime."""
    global _msvcrt_runtime
    if _msvcrt_runtime is None:
        _msvcrt_runtime = ctypes.CDLL("msvcrt.dll")
    putenv = _msvcrt_runtime._putenv
    putenv.argtypes = [ctypes.c_char_p]
    putenv.restype = ctypes.c_int
    if putenv(os.fsencode(f"{name}={value}")) != 0:
        raise OSError(f"Could not set {name} for the MSYS2 runtime")


def _load_libraries() -> Tuple[Any, Any]:
    """Load libgphoto2 and its port library for the current platform."""
    if sys.platform.startswith("win"):
        msys2_bin, camlibs, iolibs = _msys2_paths()
        _dll_directory_handles.append(os.add_dll_directory(msys2_bin))
        _set_msys2_environment("CAMLIBS", camlibs)
        _set_msys2_environment("IOLIBS", iolibs)
        return (
            ctypes.CDLL(os.path.join(msys2_bin, "libgphoto2-6.dll")),
            ctypes.CDLL(os.path.join(msys2_bin, "libgphoto2_port-12.dll")),
        )
    if sys.platform.startswith("linux"):
        try:
            return ctypes.CDLL("libgphoto2.so.6"), ctypes.CDLL("libgphoto2_port.so.12")
        except OSError:
            return ctypes.CDLL("libgphoto2.so"), ctypes.CDLL("libgphoto2_port.so")
    if sys.platform.startswith("darwin"):
        for directory in ("", "/opt/homebrew/lib", "/usr/local/lib"):
            try:
                gp = os.path.join(directory, "libgphoto2.6.dylib") if directory else "libgphoto2.6.dylib"
                port = os.path.join(directory, "libgphoto2_port.12.dylib") if directory else "libgphoto2_port.12.dylib"
                return ctypes.CDLL(gp), ctypes.CDLL(port)
            except OSError:
                continue
        raise OSError("Could not load libgphoto2 on macOS")
    raise OSError(f"Unsupported operating system: {sys.platform}")


def _configure_api(gp: Any, port: Any) -> None:
    """Declare ctypes argument and return types for used C functions."""
    p = ctypes.c_void_p
    i = ctypes.c_int
    gp.gp_context_new.restype = p
    gp.gp_context_unref.argtypes = [p]
    gp.gp_list_new.argtypes = [ctypes.POINTER(p)]
    gp.gp_list_free.argtypes = [p]
    gp.gp_list_count.argtypes = [p]
    gp.gp_list_count.restype = i
    for function in (gp.gp_list_get_name, gp.gp_list_get_value):
        function.argtypes = [p, i, ctypes.POINTER(ctypes.c_char_p)]
        function.restype = i
    gp.gp_camera_autodetect.argtypes = [p, p]
    gp.gp_camera_autodetect.restype = i
    gp.gp_result_as_string.argtypes = [i]
    gp.gp_result_as_string.restype = ctypes.c_char_p

    gp.gp_abilities_list_new.argtypes = [ctypes.POINTER(p)]
    gp.gp_abilities_list_load.argtypes = [p, p]
    gp.gp_abilities_list_lookup_model.argtypes = [p, ctypes.c_char_p]
    gp.gp_abilities_list_lookup_model.restype = i
    gp.gp_abilities_list_get_abilities.argtypes = [p, i, ctypes.POINTER(CameraAbilities)]
    gp.gp_abilities_list_free.argtypes = [p]
    port.gp_port_info_list_new.argtypes = [ctypes.POINTER(p)]
    port.gp_port_info_list_load.argtypes = [p]
    port.gp_port_info_list_lookup_path.argtypes = [p, ctypes.c_char_p]
    port.gp_port_info_list_lookup_path.restype = i
    port.gp_port_info_list_get_info.argtypes = [p, i, ctypes.POINTER(p)]
    port.gp_port_info_list_free.argtypes = [p]

    gp.gp_camera_new.argtypes = [ctypes.POINTER(p)]
    gp.gp_camera_set_abilities.argtypes = [p, CameraAbilities]
    gp.gp_camera_set_port_info.argtypes = [p, p]
    gp.gp_camera_init.argtypes = [p, p]
    gp.gp_camera_exit.argtypes = [p, p]
    gp.gp_camera_unref.argtypes = [p]
    gp.gp_camera_get_config.argtypes = [p, ctypes.POINTER(p), p]
    gp.gp_camera_set_config.argtypes = [p, p, p]
    gp.gp_camera_capture.argtypes = [p, i, ctypes.POINTER(CameraFilePath), p]
    gp.gp_camera_capture.restype = i
    gp.gp_camera_capture_preview.argtypes = [p, p, p]
    gp.gp_camera_capture_preview.restype = i
    gp.gp_camera_file_get.argtypes = [p, ctypes.c_char_p, ctypes.c_char_p, i, p, p]
    gp.gp_camera_file_delete.argtypes = [p, ctypes.c_char_p, ctypes.c_char_p, p]
    gp.gp_file_new.argtypes = [ctypes.POINTER(p)]
    gp.gp_file_free.argtypes = [p]
    gp.gp_file_get_data_and_size.argtypes = [p, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_ulong)]
    gp.gp_widget_free.argtypes = [p]
    gp.gp_widget_get_child_by_name.argtypes = [p, ctypes.c_char_p, ctypes.POINTER(p)]
    gp.gp_widget_count_children.argtypes = [p]
    gp.gp_widget_count_children.restype = i
    gp.gp_widget_get_child.argtypes = [p, i, ctypes.POINTER(p)]
    gp.gp_widget_get_name.argtypes = [p, ctypes.POINTER(ctypes.c_char_p)]
    gp.gp_widget_get_label.argtypes = [p, ctypes.POINTER(ctypes.c_char_p)]
    gp.gp_widget_get_type.argtypes = [p, ctypes.POINTER(i)]
    gp.gp_widget_get_value.argtypes = [p, p]
    gp.gp_widget_set_value.argtypes = [p, p]
    gp.gp_widget_count_choices.argtypes = [p]
    gp.gp_widget_count_choices.restype = i
    gp.gp_widget_get_choice.argtypes = [p, i, ctypes.POINTER(ctypes.c_char_p)]
    gp.gp_widget_get_range.argtypes = [p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
    gp.gp_widget_get_readonly.argtypes = [p, ctypes.POINTER(i)]


def _decode(value: Optional[bytes]) -> str:
    """Decode a nullable UTF-8 string returned by libgphoto2."""
    return value.decode("utf-8", errors="replace") if value else ""


class LibGPhotoCamera:
    """Own the libgphoto2 libraries and perform camera/config/capture operations."""

    def __init__(self) -> None:
        """Load libgphoto2 and prepare the ctypes API bindings."""
        self.gp, self.portlib = _load_libraries()
        _configure_api(self.gp, self.portlib)
        self.last_resolved_camera = None

    def _error(self, result: int, operation: str) -> RuntimeError:
        """Create a readable exception for a negative libgphoto2 result."""
        detail = _decode(self.gp.gp_result_as_string(result)) or f"error {result}"
        return RuntimeError(f"{operation}: {detail}")

    def cameras(self) -> List[Dict[str, str]]:
        """Enumerate currently connected cameras as model/port records."""
        gp = self.gp
        context = gp.gp_context_new()
        if not context:
            raise RuntimeError("Could not create a libgphoto2 context")
        camera_list = ctypes.c_void_p()
        try:
            result = gp.gp_list_new(ctypes.byref(camera_list))
            if result < 0:
                raise self._error(result, "Could not create camera list")
            result = gp.gp_camera_autodetect(camera_list, context)
            if result < 0:
                raise self._error(result, "Camera detection failed")
            found = []
            for index in range(gp.gp_list_count(camera_list)):
                model, port = ctypes.c_char_p(), ctypes.c_char_p()
                gp.gp_list_get_name(camera_list, index, ctypes.byref(model))
                gp.gp_list_get_value(camera_list, index, ctypes.byref(port))
                found.append({"model": _decode(model.value), "port": _decode(port.value)})
            return found
        finally:
            if camera_list:
                gp.gp_list_free(camera_list)
            gp.gp_context_unref(context)

    def _close(self, opened: Tuple[Any, Any, int, Any, Any]) -> None:
        """Release all libgphoto2 resources associated with an open camera."""
        context, camera, _operations, port_list, abilities_list = opened
        gp = self.gp
        gp.gp_camera_exit(camera, context)
        gp.gp_camera_unref(camera)
        self.portlib.gp_port_info_list_free(port_list)
        gp.gp_abilities_list_free(abilities_list)
        gp.gp_context_unref(context)

    def _get_config(self, camera: Any, context: Any) -> Any:
        """Fetch the camera's live configuration tree."""
        config = ctypes.c_void_p()
        result = self.gp.gp_camera_get_config(camera, ctypes.byref(config), context)
        if result < 0:
            if config:
                self.gp.gp_widget_free(config)
            raise self._error(result, "Could not read camera settings")
        return config

    def _read_value(self, widget: Any, widget_type: int) -> Any:
        """Read a widget value using the C type required by its widget kind."""
        if widget_type in (GP_WIDGET_TOGGLE, GP_WIDGET_DATE):
            value = ctypes.c_int()
            result = self.gp.gp_widget_get_value(widget, ctypes.byref(value))
            if result < 0:
                return None
            return value.value
        if widget_type == GP_WIDGET_RANGE:
            value = ctypes.c_float()
            result = self.gp.gp_widget_get_value(widget, ctypes.byref(value))
            if result < 0:
                return None
            return value.value
        value = ctypes.c_char_p()
        result = self.gp.gp_widget_get_value(widget, ctypes.byref(value))
        return _decode(value.value) if result >= 0 else None

    def _config_items(
        self, widget: Any, prefix: str = ""
    ) -> List[Dict[str, Any]]:
        """Flatten a config subtree into path-addressed setting metadata."""
        gp = self.gp
        name = ctypes.c_char_p()
        label = ctypes.c_char_p()
        widget_type = ctypes.c_int()
        gp.gp_widget_get_name(widget, ctypes.byref(name))
        gp.gp_widget_get_label(widget, ctypes.byref(label))
        gp.gp_widget_get_type(widget, ctypes.byref(widget_type))
        own_name = _decode(name.value)
        path = f"{prefix}/{own_name}" if own_name else prefix
        kind = widget_type.value
        readonly = ctypes.c_int()
        gp.gp_widget_get_readonly(widget, ctypes.byref(readonly))

        items = []
        if kind not in (GP_WIDGET_WINDOW, GP_WIDGET_SECTION, GP_WIDGET_BUTTON) and path:
            value = self._read_value(widget, kind)
            choices = []
            if kind in (GP_WIDGET_RADIO, GP_WIDGET_MENU):
                for index in range(gp.gp_widget_count_choices(widget)):
                    choice = ctypes.c_char_p()
                    if gp.gp_widget_get_choice(widget, index, ctypes.byref(choice)) >= 0:
                        choices.append(_decode(choice.value))
            item = {
                "path": path, "name": own_name,
                "label": _decode(label.value) or own_name,
                "type": kind, "value": value, "choices": choices,
                "readonly": bool(readonly.value),
            }
            if kind == GP_WIDGET_RANGE:
                low, high, step = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                if gp.gp_widget_get_range(widget, ctypes.byref(low), ctypes.byref(high), ctypes.byref(step)) >= 0:
                    item["range"] = (low.value, high.value, step.value)
            items.append(item)

        for index in range(gp.gp_widget_count_children(widget)):
            child = ctypes.c_void_p()
            if gp.gp_widget_get_child(widget, index, ctypes.byref(child)) >= 0:
                items.extend(self._config_items(child, path))
        return items

    def inspect(self, selected: Dict[str, str]) -> Dict[str, Any]:
        """Return live config metadata and advertised capture capabilities."""
        opened, config = self._open_and_config(selected)
        try:
            context, camera, operations, *_ = opened
            parameters = self._config_items(config)
            target = next(
                (item for item in parameters if item["path"].endswith("/capturetarget")),
                None,
            )
            ram_available = bool(
                target and any(self._is_ram(choice) for choice in target["choices"])
            )
            return {
                "parameters": parameters,
                "preview": bool(operations & GP_OPERATION_CAPTURE_PREVIEW),
                "capture": bool(operations & GP_OPERATION_CAPTURE_IMAGE) and ram_available,
            }
        finally:
            self.gp.gp_widget_free(config)
            self._close(opened)

    def _open_and_config(
        self, selected: Dict[str, str]
    ) -> Tuple[Tuple[Any, Any, int, Any, Any], Any]:
        """Connect and read live config, re-enumerating once if needed."""
        candidate = selected
        last_error = None
        for attempt in range(2):
            opened = None
            config = None
            try:
                opened = self._open_resources(candidate)
                config = self._get_config(opened[1], opened[0])
                self.last_resolved_camera = copy.deepcopy(candidate)
                return opened, config
            except Exception as error:
                last_error = error
                if config:
                    self.gp.gp_widget_free(config)
                if opened:
                    self._close(opened)
                if attempt:
                    break
                try:
                    discovered = self.cameras()
                except Exception as discovery_error:
                    last_error = discovery_error
                    break
                exact = [item for item in discovered if self._identity(item) == self._identity(selected)]
                same_model = [item for item in discovered if item.get("model") == selected.get("model")]
                if exact:
                    candidate = exact[0]
                elif len(same_model) == 1:
                    candidate = same_model[0]
                else:
                    break
        raise last_error or RuntimeError("Could not connect to camera")

    @staticmethod
    def _identity(camera: Dict[str, str]) -> Tuple[str, str]:
        """Return the model/port identity used to match a discovered camera."""
        return camera.get("model"), camera.get("port")

    def _open_resources(
        self, selected: Dict[str, str]
    ) -> Tuple[Any, Any, int, Any, Any]:
        """Create, configure, and initialize a camera and its C resources."""
        gp, pl = self.gp, self.portlib
        context = gp.gp_context_new()
        if not context:
            raise RuntimeError("Could not create a libgphoto2 context")
        camera = ctypes.c_void_p()
        abilities_list = ctypes.c_void_p()
        port_list = ctypes.c_void_p()
        initialized = False
        try:
            for result, operation in (
                (gp.gp_camera_new(ctypes.byref(camera)), "Could not create camera"),
                (gp.gp_abilities_list_new(ctypes.byref(abilities_list)), "Could not create camera abilities list"),
            ):
                if result < 0:
                    raise self._error(result, operation)
            result = gp.gp_abilities_list_load(abilities_list, context)
            if result < 0:
                raise self._error(result, "Could not load camera abilities")
            index = gp.gp_abilities_list_lookup_model(abilities_list, selected["model"].encode())
            if index < 0:
                raise self._error(index, f"Unsupported camera model: {selected['model']}")
            abilities = CameraAbilities()
            result = gp.gp_abilities_list_get_abilities(abilities_list, index, ctypes.byref(abilities))
            if result < 0:
                raise self._error(result, "Could not read camera abilities")
            result = gp.gp_camera_set_abilities(camera, abilities)
            if result < 0:
                raise self._error(result, "Could not configure camera model")
            result = pl.gp_port_info_list_new(ctypes.byref(port_list))
            if result < 0:
                raise self._error(result, "Could not create camera port list")
            result = pl.gp_port_info_list_load(port_list)
            if result < 0:
                raise self._error(result, "Could not load camera ports")
            index = pl.gp_port_info_list_lookup_path(port_list, selected["port"].encode())
            if index < 0:
                raise self._error(index, f"Camera port is unavailable: {selected['port']}")
            port_info = ctypes.c_void_p()
            result = pl.gp_port_info_list_get_info(port_list, index, ctypes.byref(port_info))
            if result < 0:
                raise self._error(result, "Could not read camera port information")
            result = gp.gp_camera_set_port_info(camera, port_info)
            if result < 0:
                raise self._error(result, "Could not configure camera port")
            result = gp.gp_camera_init(camera, context)
            if result < 0:
                raise self._error(result, "Could not connect to camera")
            initialized = True
            return context, camera, abilities.operations, port_list, abilities_list
        except Exception:
            if initialized:
                gp.gp_camera_exit(camera, context)
            if camera:
                gp.gp_camera_unref(camera)
            if port_list:
                pl.gp_port_info_list_free(port_list)
            if abilities_list:
                gp.gp_abilities_list_free(abilities_list)
            if context:
                gp.gp_context_unref(context)
            raise

    def _find_widget(self, root: Any, path: str) -> Any:
        """Find a setting widget by its slash-delimited config path."""
        widget = root
        for segment in path.strip("/").split("/"):
            child = ctypes.c_void_p()
            result = self.gp.gp_widget_get_child_by_name(widget, segment.encode(), ctypes.byref(child))
            if result < 0:
                raise RuntimeError(f"Camera setting no longer exists: {path}")
            widget = child
        return widget

    def _set_value(self, widget: Any, kind: int, value: Any) -> None:
        """Set a widget using a correctly typed temporary C value."""
        if kind in (GP_WIDGET_TOGGLE, GP_WIDGET_DATE):
            converted = ctypes.c_int(int(value))
            pointer = ctypes.byref(converted)
        elif kind == GP_WIDGET_RANGE:
            converted = ctypes.c_float(float(value))
            pointer = ctypes.byref(converted)
        else:
            converted = ctypes.create_string_buffer(str(value).encode("utf-8"))
            pointer = ctypes.cast(converted, ctypes.c_void_p)
        result = self.gp.gp_widget_set_value(widget, pointer)
        if result < 0:
            raise self._error(result, "Could not set camera configuration")

    def _bytes_from_file(self, camera_file: Any) -> bytes:
        """Copy image bytes from a libgphoto2 file object."""
        data, size = ctypes.c_char_p(), ctypes.c_ulong()
        result = self.gp.gp_file_get_data_and_size(camera_file, ctypes.byref(data), ctypes.byref(size))
        if result < 0:
            raise self._error(result, "Could not read image data")
        if not data or not size.value:
            raise RuntimeError("Camera returned an empty image")
        return ctypes.string_at(data, size.value)

    @staticmethod
    def _is_ram(value: Any) -> bool:
        """Return whether a capture-target label denotes internal memory."""
        normalized = str(value).lower().strip()
        return "card" not in normalized and any(token in normalized for token in ("ram", "sdram", "internal"))

    def capture(
        self,
        selected: Dict[str, str],
        settings: Dict[str, Any],
        preview: bool = False,
    ) -> bytes:
        """Apply config values, then return captured image bytes."""
        return self._capture(selected, settings, preview, {}, True)

    def _capture(
        self,
        selected: Dict[str, str],
        settings: Dict[str, Any],
        preview: bool,
        originals: Dict[str, Any],
        restore_target: bool,
    ) -> bytes:
        """Apply supported settings, enforce RAM safety, and capture an image."""
        opened, config = self._open_and_config(selected)
        context, camera, operations, *_ = opened
        original_target = None
        target_path = None
        try:
            required_operation = (
                GP_OPERATION_CAPTURE_PREVIEW if preview else GP_OPERATION_CAPTURE_IMAGE
            )
            if not operations & required_operation:
                mode = "preview" if preview else "standard"
                raise RuntimeError(f"This camera does not support {mode} capture")
            parameters = self._config_items(config)
            live = {item["path"]: item for item in parameters}
            target_item = next((item for item in parameters if item["path"].endswith("/capturetarget")), None)
            if not preview:
                if not target_item:
                    raise RuntimeError("This camera has no capture-target setting; refusing standard capture to avoid writing to its card.")
                original_target = target_item["value"]
                target_path = target_item["path"]

            changed = False
            for path, value in settings.items():
                item = live.get(path)
                if item is None or item["readonly"]:
                    continue
                if path == target_path:
                    continue
                if item["choices"] and str(value) not in item["choices"]:
                    continue
                if item.get("range"):
                    low, high, _step = item["range"]
                    try:
                        value = max(low, min(high, float(value)))
                    except (TypeError, ValueError):
                        continue
                if value == item["value"]:
                    continue
                originals.setdefault(path, item["value"])
                widget = self._find_widget(config, path)
                self._set_value(widget, item["type"], value)
                changed = True

            if not preview:
                target_widget = self._find_widget(config, target_path)
                choices = self._choices(target_widget)
                ram = next((choice for choice in choices if self._is_ram(choice)), None)
                if not ram:
                    raise RuntimeError("This camera does not offer an internal-RAM target; refusing to capture to its card.")
                target_value = self._read_value(target_widget, target_item["type"])
                if not self._is_ram(target_value):
                    if target_item["readonly"]:
                        raise RuntimeError("Camera capture target is read-only and is not internal RAM; refusing standard capture.")
                    originals.setdefault(target_path, original_target)
                    self._set_value(target_widget, target_item["type"], ram)
                    changed = True

            if changed:
                result = self.gp.gp_camera_set_config(camera, config, context)
                if result < 0:
                    raise self._error(result, "Camera rejected configuration")

            if not preview:
                # Re-read after applying config to confirm the camera accepted RAM.
                verified_config = self._get_config(camera, context)
                try:
                    verified_target = self._find_widget(verified_config, target_path)
                    verified_type = ctypes.c_int()
                    self.gp.gp_widget_get_type(verified_target, ctypes.byref(verified_type))
                    if not self._is_ram(self._read_value(verified_target, verified_type.value)):
                        raise RuntimeError("Camera did not confirm internal-RAM targeting; refusing standard capture.")
                finally:
                    self.gp.gp_widget_free(verified_config)

            if preview:
                camera_file = ctypes.c_void_p()
                result = self.gp.gp_file_new(ctypes.byref(camera_file))
                if result < 0:
                    raise self._error(result, "Could not create preview image buffer")
                try:
                    result = self.gp.gp_camera_capture_preview(camera, camera_file, context)
                    if result < 0:
                        raise self._error(result, "Camera preview capture failed")
                    return self._bytes_from_file(camera_file)
                finally:
                    self.gp.gp_file_free(camera_file)

            path = CameraFilePath()
            result = self.gp.gp_camera_capture(camera, 0, ctypes.byref(path), context)
            if result < 0:
                raise self._error(result, "Camera capture failed")
            folder = bytes(path.folder).split(b"\0", 1)[0]
            name = bytes(path.name).split(b"\0", 1)[0]
            if not folder or not name:
                raise RuntimeError("Camera capture returned an empty file path")
            camera_file = ctypes.c_void_p()
            result = self.gp.gp_file_new(ctypes.byref(camera_file))
            if result < 0:
                raise self._error(result, "Could not create image buffer")
            try:
                result = self.gp.gp_camera_file_get(camera, folder, name, GP_FILE_TYPE_NORMAL, camera_file, context)
                if result < 0:
                    raise self._error(result, "Could not retrieve captured image")
                return self._bytes_from_file(camera_file)
            finally:
                self.gp.gp_file_free(camera_file)
                self.gp.gp_camera_file_delete(camera, folder, name, context)
        finally:
            if config:
                if restore_target and original_target is not None:
                    try:
                        target_widget = self._find_widget(config, target_path)
                        current = self._read_value(target_widget, target_item["type"])
                        if current != original_target:
                            self._set_value(target_widget, target_item["type"], original_target)
                            self.gp.gp_camera_set_config(camera, config, context)
                    except Exception:
                        pass
                self.gp.gp_widget_free(config)
            self._close(opened)

    def capture_managed(
        self,
        selected: Dict[str, str],
        settings: Dict[str, Any],
        originals: Dict[str, Any],
        preview: bool = False,
        restore_target: bool = True,
    ) -> bytes:
        """Capture while recording original values for manager cleanup."""
        return self._capture(selected, settings, preview, originals, restore_target)

    def restore(self, selected: Dict[str, str], values: Dict[str, Any]) -> None:
        """Best-effort apply saved original values to a camera's live config."""
        if not values:
            return
        opened, config = self._open_and_config(selected)
        context, camera, *_ = opened
        try:
            live = {item["path"]: item for item in self._config_items(config)}
            changed = False
            for path, value in values.items():
                item = live.get(path)
                if item is None or item["readonly"] or item["value"] == value:
                    continue
                if item["choices"] and str(value) not in item["choices"]:
                    continue
                widget = self._find_widget(config, path)
                self._set_value(widget, item["type"], value)
                changed = True
            if changed:
                result = self.gp.gp_camera_set_config(camera, config, context)
                if result < 0:
                    raise self._error(result, "Could not restore camera settings")
        finally:
            self.gp.gp_widget_free(config)
            self._close(opened)

    def _choices(self, widget: Any) -> List[str]:
        """Return the current string choices advertised by a widget."""
        choices = []
        for index in range(self.gp.gp_widget_count_choices(widget)):
            choice = ctypes.c_char_p()
            if self.gp.gp_widget_get_choice(widget, index, ctypes.byref(choice)) >= 0:
                choices.append(_decode(choice.value))
        return choices


class LibGPhotoCameraManager:
    """Manage live camera state and persist per-camera config/preferences."""

    _SETTINGS_KEY = "libgphoto2/state"

    def __init__(self, camera_api: Optional[Any] = None, settings: Optional[Any] = None) -> None:
        """Create a manager, optionally with test API and settings adapters."""
        self._camera_api = camera_api
        self._settings = settings
        self._cameras = None
        self._metadata = {}
        self._persisted_metadata = set()
        self._preferences = {}
        self._original_values = {}
        self._selected = {}
        self._lock = threading.RLock()
        self._load_persisted_state()

    @staticmethod
    def camera_key(camera: Dict[str, str]) -> Tuple[str, str]:
        """Return the stable identity tuple for a camera record."""
        return LibGPhotoCamera._identity(camera)

    @property
    def last_camera(self) -> Optional[Dict[str, str]]:
        """Return a copy of the most recently selected camera, if any."""
        return copy.deepcopy(self._selected.get("last"))

    def _api(self) -> Any:
        """Lazily construct and return the low-level camera API wrapper."""
        if self._camera_api is None:
            self._camera_api = LibGPhotoCamera()
        return self._camera_api

    def _settings_store(self) -> Optional[Any]:
        """Return the injected or application-default QSettings instance."""
        if self._settings is None:
            try:
                from PySide6.QtCore import QSettings
            except ImportError:
                return None
            self._settings = QSettings()
        return self._settings

    def _load_persisted_state(self) -> None:
        """Load saved preferences and config metadata, ignoring invalid data."""
        settings = self._settings_store()
        if settings is None:
            return
        try:
            raw_state = settings.value(self._SETTINGS_KEY, "")
            if not raw_state:
                return
            state = json.loads(str(raw_state))
            if state.get("version") != 1:
                return
            for entry in state.get("preferences", []):
                key = tuple(entry["camera"])
                self._preferences[key] = entry["value"]
            for entry in state.get("metadata", []):
                key = tuple(entry["camera"])
                self._metadata[key] = entry["value"]
                self._persisted_metadata.add(key)
            for entry in state.get("selected", []):
                key = tuple(entry["camera"])
                self._selected[key] = entry["value"]
            last_camera = state.get("last_camera")
            if last_camera:
                self._selected["last"] = last_camera
        except (AttributeError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            # Invalid or old state should not prevent camera use.
            self._metadata.clear()
            self._persisted_metadata.clear()
            self._preferences.clear()
            self._selected.clear()

    def _persist_state(self) -> None:
        """Write current preferences, metadata, and selection to QSettings."""
        settings = self._settings_store()
        if settings is None:
            return
        state = {
            "version": 1,
            "preferences": [
                {"camera": list(key), "value": value}
                for key, value in self._preferences.items()
            ],
            "metadata": [
                {"camera": list(key), "value": value}
                for key, value in self._metadata.items()
            ],
            "selected": [
                {"camera": list(key), "value": value}
                for key, value in self._selected.items()
                if key != "last"
            ],
            "last_camera": self._selected.get("last"),
        }
        try:
            settings.setValue(
                self._SETTINGS_KEY,
                json.dumps(state, ensure_ascii=False, allow_nan=False),
            )
        except (TypeError, ValueError):
            # Keep the in-memory preferences even if a camera exposes a value
            # that cannot be represented in the persistent JSON cache.
            return

    def cameras(self, refresh: bool = False) -> List[Dict[str, str]]:
        """Return detected cameras, optionally refreshing the discovery cache."""
        with self._lock:
            if refresh or self._cameras is None:
                self._cameras = copy.deepcopy(self._api().cameras())
            return copy.deepcopy(self._cameras)

    def inspect(
        self, selected: Dict[str, str], refresh: bool = False
    ) -> Dict[str, Any]:
        """Return cached config metadata or refresh it from the selected camera."""
        key = self.camera_key(selected)
        with self._lock:
            needs_live_inspection = refresh or key not in self._metadata or key in self._persisted_metadata
            if needs_live_inspection:
                try:
                    api = self._api()
                    api.last_resolved_camera = None
                    self._metadata[key] = api.inspect(selected)
                    self._persisted_metadata.discard(key)
                    self._adopt_resolved_camera(key, selected)
                    self._persist_state()
                except Exception:
                    if key not in self._persisted_metadata:
                        raise
                    # Saved schemas can keep the controls useful while a
                    # camera is asleep or temporarily unreachable. They are
                    # marked as cached so the UI will not offer capture.
                    cached = copy.deepcopy(self._metadata[key])
                    cached["_cached"] = True
                    return cached
            return copy.deepcopy(self._metadata.get(self.camera_key(selected), {}))

    def preferences(self, selected: Dict[str, str]) -> Dict[str, Any]:
        """Return a deep copy of preferences saved for a camera."""
        with self._lock:
            return copy.deepcopy(self._preferences.get(self.camera_key(selected), {}))

    def save_preferences(
        self,
        selected: Dict[str, str],
        settings: Dict[str, Any],
        preview: bool,
        restore_target: bool,
    ) -> None:
        """Save setting values and capture options for a camera."""
        key = self.camera_key(selected)
        with self._lock:
            self._preferences[key] = {
                "settings": copy.deepcopy(settings),
                "preview": bool(preview),
                "restore_target": bool(restore_target),
            }
            self._selected["last"] = copy.deepcopy(selected)
            self._selected[key] = copy.deepcopy(selected)
            self._persist_state()

    def capture(
        self,
        selected: Dict[str, str],
        settings: Dict[str, Any],
        preview: bool = False,
        restore_target: bool = True,
    ) -> bytes:
        """Capture with the manager's original-value restoration tracking."""
        key = self.camera_key(selected)
        with self._lock:
            self._selected["last"] = copy.deepcopy(selected)
            self._selected[key] = copy.deepcopy(selected)
            originals = self._original_values.setdefault(key, {})
            api = self._api()
            api.last_resolved_camera = None
            try:
                image = api.capture_managed(
                    selected, settings, originals, preview, restore_target
                )
            finally:
                self._adopt_resolved_camera(key, selected, originals)
            current = self._preferences.setdefault(key, {})
            current.update({
                "settings": copy.deepcopy(settings),
                "preview": bool(preview),
                "restore_target": bool(restore_target),
            })
            resolved_key = self.camera_key(selected)
            if resolved_key != key:
                self._preferences[resolved_key] = self._preferences.pop(key)
            self._persist_state()
            return image

    def _adopt_resolved_camera(
        self,
        old_key: Tuple[str, str],
        selected: Dict[str, str],
        originals: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Move cached state to a camera identity resolved after re-enumeration."""
        resolved = getattr(self._camera_api, "last_resolved_camera", None)
        if not resolved:
            return
        new_key = self.camera_key(resolved)
        if new_key == old_key:
            return
        if isinstance(selected, dict):
            selected.update(resolved)
        self._selected.pop(old_key, None)
        self._selected[new_key] = copy.deepcopy(resolved)
        self._selected["last"] = copy.deepcopy(resolved)
        if old_key in self._preferences:
            self._preferences.setdefault(new_key, self._preferences[old_key])
            self._preferences.pop(old_key, None)
        if old_key in self._metadata:
            self._metadata.setdefault(new_key, self._metadata[old_key])
            self._metadata.pop(old_key, None)
        old_originals = self._original_values.pop(old_key, {})
        if originals is not None:
            old_originals.update(originals)
        if old_originals:
            new_originals = self._original_values.setdefault(new_key, {})
            for path, value in old_originals.items():
                new_originals.setdefault(path, value)

    def reevaluate(self) -> List[Dict[str, str]]:
        """Best-effort restore, then refresh camera and config discovery."""
        with self._lock:
            self._restore_all()
            self._metadata.clear()
            self._persisted_metadata.clear()
            self._cameras = None
            self._persist_state()
            return self.cameras(refresh=True)

    def release_camera(self, selected: Dict[str, str]) -> None:
        """Restore this camera's original values and forget its change snapshot."""
        key = self.camera_key(selected)
        with self._lock:
            values = self._original_values.get(key)
            if not values:
                return
            try:
                self._api().restore(selected, values)
            except Exception:
                # Keep the snapshot so a later release/shutdown can retry.
                raise
            self._original_values.pop(key, None)

    def _restore_all(self) -> None:
        """Best-effort restore all settings changed during this process."""
        if self._camera_api is None:
            return
        pending = {}
        for key, values in list(self._original_values.items()):
            selected = self._selected.get(key)
            if not selected or not values:
                continue
            try:
                self._camera_api.restore(selected, values)
            except Exception:
                # The camera may be disconnected or asleep; cleanup is best-effort.
                pending[key] = values
        self._original_values = pending

    def close(self) -> None:
        """Restore camera settings and flush persistent application settings."""
        with self._lock:
            self._restore_all()
            settings = self._settings_store()
            if settings is not None and hasattr(settings, "sync"):
                settings.sync()


_default_manager = None
_default_manager_lock = threading.Lock()


def get_default_camera_manager() -> LibGPhotoCameraManager:
    """Return the lazily-created manager shared by dialogs in this process."""
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = LibGPhotoCameraManager()
        return _default_manager


def _close_default_manager() -> None:
    """Restore camera state held by the process-wide default manager."""
    if _default_manager is not None:
        _default_manager.close()


atexit.register(_close_default_manager)
