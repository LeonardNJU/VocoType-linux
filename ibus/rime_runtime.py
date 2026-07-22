#!/usr/bin/env python3
"""Small, version-tolerant ctypes adapter for the stable librime C API.

VoCoType only needs a narrow subset of librime: create/destroy a session,
forward keys, read commits/context, clear composition, and choose a schema.
Keeping that adapter in-tree avoids a compiled Python binding and lets the
same code run against the distro librime on Ubuntu, Fedora, and Arch.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes as c
import ctypes.util
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class RimeRuntimeError(RuntimeError):
    """Raised when librime cannot be loaded or initialized safely."""


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    comment: str = ""


@dataclass(frozen=True, slots=True)
class Composition:
    length: int
    cursor_pos: int
    sel_start: int
    sel_end: int
    preedit: str


@dataclass(frozen=True, slots=True)
class Menu:
    page_size: int
    page_no: int
    is_last_page: bool
    highlighted_candidate_index: int
    num_candidates: int
    select_keys: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class Context:
    composition: Composition
    menu: Menu


@dataclass(frozen=True, slots=True)
class Commit:
    text: str


class _RimeTraits(c.Structure):
    # Prefix shared by librime 1.7 and current releases. Newer members are
    # appended by librime; data_size tells the library which prefix we provide.
    _fields_ = [
        ("data_size", c.c_int),
        ("shared_data_dir", c.c_char_p),
        ("user_data_dir", c.c_char_p),
        ("distribution_name", c.c_char_p),
        ("distribution_code_name", c.c_char_p),
        ("distribution_version", c.c_char_p),
        ("app_name", c.c_char_p),
        ("modules", c.POINTER(c.c_char_p)),
        ("min_log_level", c.c_int),
        ("log_dir", c.c_char_p),
        ("prebuilt_data_dir", c.c_char_p),
        ("staging_dir", c.c_char_p),
    ]


class _RimeComposition(c.Structure):
    _fields_ = [
        ("length", c.c_int),
        ("cursor_pos", c.c_int),
        ("sel_start", c.c_int),
        ("sel_end", c.c_int),
        ("preedit", c.c_char_p),
    ]


class _RimeCandidate(c.Structure):
    _fields_ = [
        ("text", c.c_char_p),
        ("comment", c.c_char_p),
        ("reserved", c.c_void_p),
    ]


class _RimeMenu(c.Structure):
    _fields_ = [
        ("page_size", c.c_int),
        ("page_no", c.c_int),
        ("is_last_page", c.c_int),
        ("highlighted_candidate_index", c.c_int),
        ("num_candidates", c.c_int),
        ("candidates", c.POINTER(_RimeCandidate)),
        ("select_keys", c.c_char_p),
    ]


class _RimeContext(c.Structure):
    _fields_ = [
        ("data_size", c.c_int),
        ("composition", _RimeComposition),
        ("menu", _RimeMenu),
        ("commit_text_preview", c.c_char_p),
        ("select_labels", c.POINTER(c.c_char_p)),
    ]


class _RimeCommit(c.Structure):
    _fields_ = [
        ("data_size", c.c_int),
        ("text", c.c_char_p),
    ]


_SetupFn = c.CFUNCTYPE(None, c.POINTER(_RimeTraits))
_InitializeFn = c.CFUNCTYPE(None, c.POINTER(_RimeTraits))
_FinalizeFn = c.CFUNCTYPE(None)
_CreateSessionFn = c.CFUNCTYPE(c.c_size_t)
_DestroySessionFn = c.CFUNCTYPE(c.c_int, c.c_size_t)
_ProcessKeyFn = c.CFUNCTYPE(c.c_int, c.c_size_t, c.c_int, c.c_int)
_ClearCompositionFn = c.CFUNCTYPE(None, c.c_size_t)
_GetCommitFn = c.CFUNCTYPE(c.c_int, c.c_size_t, c.POINTER(_RimeCommit))
_FreeCommitFn = c.CFUNCTYPE(c.c_int, c.POINTER(_RimeCommit))
_GetContextFn = c.CFUNCTYPE(c.c_int, c.c_size_t, c.POINTER(_RimeContext))
_FreeContextFn = c.CFUNCTYPE(c.c_int, c.POINTER(_RimeContext))
_GetCurrentSchemaFn = c.CFUNCTYPE(
    c.c_int, c.c_size_t, c.c_char_p, c.c_size_t
)
_SelectSchemaFn = c.CFUNCTYPE(c.c_int, c.c_size_t, c.c_char_p)


class _RimeApiPrefix(c.Structure):
    """Stable RimeApi prefix through schema selection (librime 1.0+)."""

    _fields_ = [
        ("data_size", c.c_int),
        ("setup", _SetupFn),
        ("set_notification_handler", c.c_void_p),
        ("initialize", _InitializeFn),
        ("finalize", _FinalizeFn),
        ("start_maintenance", c.c_void_p),
        ("is_maintenance_mode", c.c_void_p),
        ("join_maintenance_thread", c.c_void_p),
        ("deployer_initialize", c.c_void_p),
        ("prebuild", c.c_void_p),
        ("deploy", c.c_void_p),
        ("deploy_schema", c.c_void_p),
        ("deploy_config_file", c.c_void_p),
        ("sync_user_data", c.c_void_p),
        ("create_session", _CreateSessionFn),
        ("find_session", c.c_void_p),
        ("destroy_session", _DestroySessionFn),
        ("cleanup_stale_sessions", c.c_void_p),
        ("cleanup_all_sessions", c.c_void_p),
        ("process_key", _ProcessKeyFn),
        ("commit_composition", c.c_void_p),
        ("clear_composition", _ClearCompositionFn),
        ("get_commit", _GetCommitFn),
        ("free_commit", _FreeCommitFn),
        ("get_context", _GetContextFn),
        ("free_context", _FreeContextFn),
        ("get_status", c.c_void_p),
        ("free_status", c.c_void_p),
        ("set_option", c.c_void_p),
        ("get_option", c.c_void_p),
        ("set_property", c.c_void_p),
        ("get_property", c.c_void_p),
        ("get_schema_list", c.c_void_p),
        ("free_schema_list", c.c_void_p),
        ("get_current_schema", _GetCurrentSchemaFn),
        ("select_schema", _SelectSchemaFn),
    ]


_MAX_CANDIDATES: Final = 100
_RUNTIME_LOCK = threading.RLock()
_RUNTIME: RimeRuntime | None = None


def _struct_data_size(struct_type: type[c.Structure]) -> int:
    return c.sizeof(struct_type) - c.sizeof(c.c_int)


def _decode(value: bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if value else ""


def _library_candidates() -> tuple[str, ...]:
    discovered = ctypes.util.find_library("rime")
    ordered = [discovered, "librime.so.1", "librime.so"]
    return tuple(dict.fromkeys(item for item in ordered if item))


def _load_library() -> c.CDLL:
    errors: list[str] = []
    for candidate in _library_candidates():
        try:
            return c.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    raise RimeRuntimeError("cannot load librime: " + "; ".join(errors))


def librime_available() -> bool:
    try:
        library = _load_library()
        direct = all(
            hasattr(library, symbol)
            for symbol in (
                "RimeSetup",
                "RimeInitialize",
                "RimeCreateSession",
                "RimeProcessKey",
                "RimeGetContext",
            )
        )
        return direct or hasattr(library, "rime_get_api")
    except (OSError, RimeRuntimeError):
        return False


class RimeRuntime:
    """One process-global librime runtime with multiple independent sessions."""

    def __init__(
        self,
        *,
        shared_data_dir: str | Path,
        user_data_dir: str | Path,
        log_dir: str | Path,
        distribution_version: str = "3",
        library: c.CDLL | None = None,
    ) -> None:
        self.shared_data_dir = Path(shared_data_dir).expanduser().resolve()
        self.user_data_dir = Path(user_data_dir).expanduser().resolve()
        self.log_dir = Path(log_dir).expanduser().resolve()
        for directory in (self.shared_data_dir, self.user_data_dir, self.log_dir):
            if not directory.is_dir():
                raise RimeRuntimeError(f"Rime directory does not exist: {directory}")

        self.prebuilt_data_dir = self.shared_data_dir / "build"
        self.staging_dir = self.user_data_dir / "build"
        if not (self.shared_data_dir / "default.yaml").is_file():
            raise RimeRuntimeError(
                f"Rime shared default.yaml is missing: {self.shared_data_dir}"
            )
        if not (self.staging_dir / "default.yaml").is_file():
            raise RimeRuntimeError(
                f"Rime user data is not deployed: {self.staging_dir / 'default.yaml'}"
            )

        self._lock = threading.RLock()
        self._library = library or _load_library()
        self._configure_signatures()
        self._active_sessions: set[int] = set()
        self._finalized = False

        # Keep every encoded string and the module array alive for the complete
        # librime lifetime. Some old releases retain pointers from RimeTraits.
        self._strings = tuple(
            value.encode("utf-8")
            for value in (
                str(self.shared_data_dir),
                str(self.user_data_dir),
                "VoCoType",
                "vocotype",
                distribution_version,
                "rime.vocotype",
                str(self.log_dir),
                str(self.prebuilt_data_dir),
                str(self.staging_dir),
            )
        )
        self._modules = (c.c_char_p * 2)(b"default", None)
        self._traits = _RimeTraits()
        self._traits.data_size = _struct_data_size(_RimeTraits)
        (
            self._traits.shared_data_dir,
            self._traits.user_data_dir,
            self._traits.distribution_name,
            self._traits.distribution_code_name,
            self._traits.distribution_version,
            self._traits.app_name,
        ) = self._strings[:6]
        self._traits.modules = c.cast(self._modules, c.POINTER(c.c_char_p))
        self._traits.min_log_level = 2
        self._traits.log_dir = self._strings[6]
        self._traits.prebuilt_data_dir = self._strings[7]
        self._traits.staging_dir = self._strings[8]

        with self._lock:
            self._setup(c.byref(self._traits))
            self._initialize(c.byref(self._traits))
        atexit.register(self.finalize)

    @property
    def key(self) -> tuple[Path, Path, Path]:
        return self.shared_data_dir, self.user_data_dir, self.log_dir

    def _configure_signatures(self) -> None:
        lib = self._library
        if hasattr(lib, "RimeSetup"):
            self._api_pointer = None

            def bind(name: str, argtypes: list[object], restype: object):
                function = getattr(lib, name)
                function.argtypes = argtypes
                function.restype = restype
                return function

            self._setup = bind("RimeSetup", [c.POINTER(_RimeTraits)], None)
            self._initialize = bind(
                "RimeInitialize", [c.POINTER(_RimeTraits)], None
            )
            self._finalize = bind("RimeFinalize", [], None)
            self._create_session = bind("RimeCreateSession", [], c.c_size_t)
            self._destroy_session = bind(
                "RimeDestroySession", [c.c_size_t], c.c_int
            )
            self._process_key = bind(
                "RimeProcessKey",
                [c.c_size_t, c.c_int, c.c_int],
                c.c_int,
            )
            self._get_commit = bind(
                "RimeGetCommit",
                [c.c_size_t, c.POINTER(_RimeCommit)],
                c.c_int,
            )
            self._free_commit = bind(
                "RimeFreeCommit", [c.POINTER(_RimeCommit)], c.c_int
            )
            self._get_context = bind(
                "RimeGetContext",
                [c.c_size_t, c.POINTER(_RimeContext)],
                c.c_int,
            )
            self._free_context = bind(
                "RimeFreeContext", [c.POINTER(_RimeContext)], c.c_int
            )
            self._select_schema = bind(
                "RimeSelectSchema", [c.c_size_t, c.c_char_p], c.c_int
            )
            self._get_current_schema = bind(
                "RimeGetCurrentSchema",
                [c.c_size_t, c.c_char_p, c.c_size_t],
                c.c_int,
            )
            self._clear_composition = bind(
                "RimeClearComposition", [c.c_size_t], None
            )
            return

        if not hasattr(lib, "rime_get_api"):
            raise RimeRuntimeError(
                "librime exports neither direct C functions nor rime_get_api"
            )
        lib.rime_get_api.argtypes = []
        lib.rime_get_api.restype = c.POINTER(_RimeApiPrefix)
        pointer = lib.rime_get_api()
        if not pointer:
            raise RimeRuntimeError("rime_get_api returned a null pointer")
        api = pointer.contents
        required_size = (
            _RimeApiPrefix.select_schema.offset + c.sizeof(c.c_void_p)
            - c.sizeof(c.c_int)
        )
        if int(api.data_size) < required_size:
            raise RimeRuntimeError(
                "librime RimeApi prefix is too old: "
                f"data_size={api.data_size} required={required_size}"
            )
        for name in (
            "setup",
            "initialize",
            "finalize",
            "create_session",
            "destroy_session",
            "process_key",
            "get_commit",
            "free_commit",
            "get_context",
            "free_context",
            "select_schema",
            "get_current_schema",
            "clear_composition",
        ):
            if not getattr(api, name):
                raise RimeRuntimeError(f"librime API function is unavailable: {name}")
        self._api_pointer = pointer
        self._setup = api.setup
        self._initialize = api.initialize
        self._finalize = api.finalize
        self._create_session = api.create_session
        self._destroy_session = api.destroy_session
        self._process_key = api.process_key
        self._get_commit = api.get_commit
        self._free_commit = api.free_commit
        self._get_context = api.get_context
        self._free_context = api.free_context
        self._select_schema = api.select_schema
        self._get_current_schema = api.get_current_schema
        self._clear_composition = api.clear_composition

    def create_session(self) -> RimeSession:
        with self._lock:
            self._require_live()
            session_id = int(self._create_session())
            if session_id == 0:
                raise RimeRuntimeError("librime failed to create a session")
            self._active_sessions.add(session_id)
        return RimeSession(runtime=self, session_id=session_id)

    def destroy_session(self, session_id: int) -> None:
        with self._lock:
            if self._finalized or session_id not in self._active_sessions:
                return
            self._destroy_session(c.c_size_t(session_id))
            self._active_sessions.discard(session_id)

    def process_key(self, session_id: int, keycode: int, mask: int) -> bool:
        with self._lock:
            self._require_session(session_id)
            return bool(self._process_key(session_id, keycode, mask))

    def get_commit(self, session_id: int) -> Commit | None:
        with self._lock:
            self._require_session(session_id)
            commit = _RimeCommit()
            commit.data_size = _struct_data_size(_RimeCommit)
            if not self._get_commit(session_id, c.byref(commit)):
                return None
            try:
                return Commit(text=_decode(commit.text))
            finally:
                self._free_commit(c.byref(commit))

    def get_context(self, session_id: int) -> Context | None:
        with self._lock:
            self._require_session(session_id)
            raw = _RimeContext()
            raw.data_size = _struct_data_size(_RimeContext)
            if not self._get_context(session_id, c.byref(raw)):
                return None
            try:
                composition = Composition(
                    length=int(raw.composition.length),
                    cursor_pos=int(raw.composition.cursor_pos),
                    sel_start=int(raw.composition.sel_start),
                    sel_end=int(raw.composition.sel_end),
                    preedit=_decode(raw.composition.preedit),
                )
                candidate_count = max(
                    0, min(int(raw.menu.num_candidates), _MAX_CANDIDATES)
                )
                candidates: list[Candidate] = []
                if raw.menu.candidates:
                    for index in range(candidate_count):
                        item = raw.menu.candidates[index]
                        candidates.append(
                            Candidate(
                                text=_decode(item.text),
                                comment=_decode(item.comment),
                            )
                        )
                menu = Menu(
                    page_size=max(1, int(raw.menu.page_size)),
                    page_no=max(0, int(raw.menu.page_no)),
                    is_last_page=bool(raw.menu.is_last_page),
                    highlighted_candidate_index=max(
                        0, int(raw.menu.highlighted_candidate_index)
                    ),
                    num_candidates=int(raw.menu.num_candidates),
                    select_keys=_decode(raw.menu.select_keys),
                    candidates=tuple(candidates),
                )
                return Context(composition=composition, menu=menu)
            finally:
                self._free_context(c.byref(raw))

    def select_schema(self, session_id: int, schema_id: str) -> bool:
        encoded = schema_id.encode("utf-8")
        with self._lock:
            self._require_session(session_id)
            return bool(self._select_schema(session_id, encoded))

    def get_current_schema(self, session_id: int) -> str:
        buffer = c.create_string_buffer(1024)
        with self._lock:
            self._require_session(session_id)
            if not self._get_current_schema(session_id, buffer, c.sizeof(buffer)):
                return ""
        return _decode(buffer.value)

    def clear_composition(self, session_id: int) -> None:
        with self._lock:
            self._require_session(session_id)
            self._clear_composition(session_id)

    def finalize(self) -> None:
        with self._lock:
            if self._finalized:
                return
            for session_id in tuple(self._active_sessions):
                self._destroy_session(c.c_size_t(session_id))
            self._active_sessions.clear()
            self._finalize()
            self._finalized = True

    def _require_live(self) -> None:
        if self._finalized:
            raise RimeRuntimeError("librime runtime has already been finalized")

    def _require_session(self, session_id: int) -> None:
        self._require_live()
        if session_id not in self._active_sessions:
            raise RimeRuntimeError(f"unknown or closed Rime session: {session_id}")


class RimeSession:
    def __init__(self, *, runtime: RimeRuntime, session_id: int) -> None:
        self.runtime = runtime
        self.id = session_id
        self._closed = False

    def process_key(self, keycode: int, mask: int) -> bool:
        return self.runtime.process_key(self.id, keycode, mask)

    def get_commit(self) -> Commit | None:
        return self.runtime.get_commit(self.id)

    def get_context(self) -> Context | None:
        return self.runtime.get_context(self.id)

    def select_schema(self, schema_id: str) -> bool:
        return self.runtime.select_schema(self.id, schema_id)

    def get_current_schema(self) -> str:
        return self.runtime.get_current_schema(self.id)

    def clear_composition(self) -> None:
        self.runtime.clear_composition(self.id)

    def close(self) -> None:
        if self._closed:
            return
        self.runtime.destroy_session(self.id)
        self._closed = True


def get_runtime(
    *,
    shared_data_dir: str | Path,
    user_data_dir: str | Path,
    log_dir: str | Path,
    distribution_version: str = "3",
) -> RimeRuntime:
    global _RUNTIME
    requested_key = tuple(
        Path(value).expanduser().resolve()
        for value in (shared_data_dir, user_data_dir, log_dir)
    )
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = RimeRuntime(
                shared_data_dir=requested_key[0],
                user_data_dir=requested_key[1],
                log_dir=requested_key[2],
                distribution_version=distribution_version,
            )
        elif _RUNTIME.key != requested_key:
            raise RimeRuntimeError(
                "librime is already initialized for a different data directory: "
                f"active={_RUNTIME.key} requested={requested_key}"
            )
        return _RUNTIME


def probe_runtime(
    *,
    shared_data_dir: str | Path,
    user_data_dir: str | Path,
    log_dir: str | Path,
    schema: str = "luna_pinyin",
    key: str = "n",
) -> Context:
    if len(key) != 1:
        raise ValueError("probe key must contain exactly one character")
    runtime = get_runtime(
        shared_data_dir=shared_data_dir,
        user_data_dir=user_data_dir,
        log_dir=log_dir,
    )
    session = runtime.create_session()
    try:
        if not session.select_schema(schema):
            raise RimeRuntimeError(f"cannot select Rime schema: {schema}")
        if not session.process_key(ord(key), 0):
            raise RimeRuntimeError(f"Rime did not handle probe key: {key!r}")
        context = session.get_context()
        if context is None or not context.composition.preedit:
            raise RimeRuntimeError("Rime probe produced no preedit")
        return context
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe VoCoType's librime adapter")
    parser.add_argument("--shared-data-dir", type=Path, required=True)
    parser.add_argument("--user-data-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--schema", default="luna_pinyin")
    parser.add_argument("--key", default="n")
    args = parser.parse_args()
    context = probe_runtime(
        shared_data_dir=args.shared_data_dir,
        user_data_dir=args.user_data_dir,
        log_dir=args.log_dir,
        schema=args.schema,
        key=args.key,
    )
    candidates = ",".join(item.text for item in context.menu.candidates[:5])
    print(
        "RIME_RUNTIME_OK "
        f"schema={args.schema} preedit={context.composition.preedit!r} "
        f"candidates={candidates}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
