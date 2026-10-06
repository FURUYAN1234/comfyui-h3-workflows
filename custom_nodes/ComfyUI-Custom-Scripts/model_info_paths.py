"""Filesystem boundaries for model metadata, examples and previews.

Model leaf symlinks are supported; sidecars stay beside the registered model
name. Directory links may not escape that model's registered directory.
"""
from contextlib import contextmanager
import locale
import ntpath
import os
import shutil
import stat
import tempfile

import folder_paths


class InvalidPath(ValueError):
    pass


def relative_name(value, *, nested=True):
    """Validate both Windows and POSIX syntax, regardless of server platform."""
    if not isinstance(value, str) or not value or ntpath.splitdrive(value)[0]:
        raise InvalidPath("A relative name is required")
    parts = value.replace("\\", "/").split("/")
    if not nested and len(parts) != 1:
        raise InvalidPath("A file name without directories is required")
    for part in parts:
        device = part.split(".", 1)[0].upper()
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or device in ("CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$")
                or device in {"COM" + i for i in "123456789¹²³"}
                or device in {"LPT" + i for i in "123456789¹²³"}):
            raise InvalidPath("Invalid file name")
    return os.path.join(*parts)


def inside(root, path):
    try:
        return os.path.normcase(os.path.commonpath((root, path))) == os.path.normcase(root)
    except ValueError:
        return False


def is_link(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    # Python 3.10 islink() misses Windows junctions. Reject reparse points at
    # sidecar entries even if they redirect to another model in the same root.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def child(root, name):
    """root is a previously resolved, trusted directory, not request input."""
    path = os.path.abspath(os.path.join(root, relative_name(name)))
    if not inside(root, os.path.realpath(os.path.dirname(path))):
        raise InvalidPath("Directory leaves the allowed folder")
    # Reject dangling links too; isfile alone would miss them.
    if is_link(path) or not inside(root, os.path.realpath(path)):
        raise InvalidPath("Sidecar leaves the allowed folder")
    return path


class ModelFile:
    def __init__(self, kind, path):
        self.kind = kind
        self.parent = os.path.realpath(os.path.dirname(path))
        self.path = os.path.join(self.parent, os.path.basename(path))
        self.stem = os.path.splitext(os.path.basename(path))[0]

    def sidecar(self, suffix):
        return child(self.parent, self.stem + suffix)


def resolve_model(route_name, *, match_stem=False):
    if not isinstance(route_name, str) or "/" not in route_name:
        raise InvalidPath("A model category and name are required")
    kind, name = route_name.split("/", 1)
    relative_name(kind, nested=False)
    name = relative_name(name)
    try:
        roots = folder_paths.get_folder_paths(kind)
    except KeyError as exc:
        raise InvalidPath("Unknown model category") from exc
    if not roots:
        raise InvalidPath("Unknown model category")
    candidate = None
    if match_stem and kind in ("loras", "embeddings"):
        for listed_name in folder_paths.get_filename_list(kind):
            try:
                listed = relative_name(listed_name)
            except InvalidPath:
                continue
            if name.lower() in (listed.lower(), os.path.splitext(listed)[0].lower()):
                candidate = folder_paths.get_full_path(kind, listed_name)
                if candidate:
                    break
    else:
        candidate = folder_paths.get_full_path(kind, name)
    if not candidate:
        raise FileNotFoundError("Model not found")
    candidate = os.path.abspath(candidate)
    # Validate the location lexically and through its real parent. A leaf
    # model symlink is intentional user configuration and stays supported.
    if not any(inside(os.path.abspath(root), candidate)
               and inside(os.path.realpath(root), os.path.realpath(os.path.dirname(candidate)))
               for root in roots):
        raise InvalidPath("Model leaves the registered folder")
    if not os.path.isfile(candidate):
        raise FileNotFoundError("Model not found")
    return ModelFile(kind, candidate)


@contextmanager
def atomic_output(root, path):
    """Replace the directory entry; never truncate a linked destination inode."""
    name = os.path.relpath(path, root)
    path = child(root, name)
    descriptor, temporary = tempfile.mkstemp(prefix=".pysssss-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as output:
            yield output
        child(root, name)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_text(root, path, text):
    if not isinstance(text, str):
        raise InvalidPath("Text is required")
    with atomic_output(root, path) as output:
        output.write(text.encode("utf-8"))


def read_notes(path):
    with open(path, "rb") as file:
        content = file.read()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        # Earlier versions used the platform encoding (e.g. cp932 on Windows).
        return content.decode(locale.getpreferredencoding(False))


def copy_preview(source_root, source, model, extension):
    source = child(source_root, os.path.relpath(source, source_root))
    with open(source, "rb") as input_file:
        with atomic_output(model.parent, model.sidecar(extension)) as output:
            shutil.copyfileobj(input_file, output)
