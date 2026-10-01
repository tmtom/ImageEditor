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

