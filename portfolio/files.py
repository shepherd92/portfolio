"""Small file and JSON primitives shared by the input stores."""

from datetime import date
import json
import os
from pathlib import Path
import tempfile


def read_optional_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def stage_bytes(path, contents, *, preserve_mode):
    """Flush a sibling temporary file, removing it if staging fails."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        if preserve_mode and path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def json_bytes(value):
    return (json.dumps(value, default=_json_month, ensure_ascii=False,
                       allow_nan=False, indent=2) + '\n').encode('utf-8')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate field: {key}.')
        result[key] = value
    return result


def _json_month(value):
    if type(value) is date:
        return value.isoformat()[:7]
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
