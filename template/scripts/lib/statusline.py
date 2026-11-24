"""Claude Code status line: show the session model and quota, and log a usage record for the console.

Claude Code runs `statusline.sh`, which execs this module with the session JSON on stdin. The output
is one short line; a compact record goes to $WSC_HOME/claude-statusline.jsonl (default
~/.workspace-console) when rate limits are present. Exit code is always 0.

Public API: main() -> 0, install(settings, command, replace=False) -> 'ajouté' | 'identique' |
'conservé' | 'remplacé' (raises ValueError on an unreadable settings file, which is never written).
"""

import datetime
import json
import os
import stat
import sys
import tempfile

MAX_INPUT_BYTES = 262144
MAX_FILE_BYTES = 65536
FILE_NAME = 'claude-statusline.jsonl'
DEFAULT_LABEL = 'Claude'
WINDOWS = (('five_hour', '5 h'), ('seven_day', 'semaine'))
LABEL_MAX = 60
UTC = datetime.UTC


def main():
    try:
        text = render(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
    except Exception:
        text = DEFAULT_LABEL
    try:
        print(text)
        sys.stdout.flush()
    except Exception:
        silence_stdout()
    return 0


def silence_stdout():
    """Point descriptor 1 at os.devnull so that a closed or broken output raises nothing at exit."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.close(null)
    except OSError:
        pass


def render(raw):
    """Return the status line for the raw session JSON, and log a record when rate limits are present."""
    data = parse(raw)
    if data is None:
        return DEFAULT_LABEL
    label = model_label(data.get('model'))
    limits = data.get('rate_limits')
    if not isinstance(limits, dict):
        return label
    windows = {key: window(limits.get(key)) for key, _ in WINDOWS}
    append_record(windows)
    text = label
    for key, name in WINDOWS:
        if windows[key] is not None:
            text += ' · {} : {} %'.format(name, int(windows[key]['used_percentage'] + 0.5))
    return text


def parse(raw):
    if len(raw) > MAX_INPUT_BYTES:
        return None
    try:
        data = json.loads(raw.decode('utf-8'))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def model_label(model):
    if not isinstance(model, dict):
        return DEFAULT_LABEL
    for key in ('display_name', 'id'):
        value = model.get(key)
        if isinstance(value, str):
            cleaned = ''.join(char for char in value if 0x20 <= ord(char) <= 0x7E)[:LABEL_MAX]
            if cleaned:
                return cleaned
    return DEFAULT_LABEL


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def window(value):
    """Return {'used_percentage', 'resets_at'} for a valid window, None otherwise."""
    if not isinstance(value, dict):
        return None
    used = value.get('used_percentage')
    resets = utc_stamp(value.get('resets_at'))
    if not is_number(used) or not 0 <= used <= 100 or resets is None:
        return None
    return {'used_percentage': used, 'resets_at': resets}


def utc_stamp(value):
    """Epoch seconds or timezone-aware ISO 8601 string as AAAA-MM-JJTHH:MM:SS.mmmZ, None if invalid."""
    try:
        if is_number(value) and value > 0:
            moment = datetime.datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if moment.tzinfo is None:
                return None
            moment = moment.astimezone(UTC)
        else:
            return None
    except (OverflowError, OSError, ValueError):
        return None
    return format_stamp(moment)


def format_stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S') + f'.{moment.microsecond // 1000:03d}Z'


def append_record(windows):
    record = {
        'timestamp': format_stamp(datetime.datetime.now(UTC)),
        'five_hour': windows['five_hour'],
        'seven_day': windows['seven_day'],
    }
    line = (json.dumps(record, separators=(',', ':')) + '\n').encode('utf-8')
    try:
        folder = os.environ.get('WSC_HOME') or os.path.join(os.path.expanduser('~'), '.workspace-console')
        os.makedirs(folder, mode=0o700, exist_ok=True)
        path = os.path.join(folder, FILE_NAME)
        fd = open_log(path)
        if fd is None:
            return
        if os.fstat(fd).st_size + len(line) > MAX_FILE_BYTES:
            os.close(fd)
            os.replace(path, path + '.1')
            fd = open_log(path)
            if fd is None:
                return
        with os.fdopen(fd, 'ab') as handle:
            handle.write(line)
    except OSError:
        pass


def open_log(path):
    """Descriptor of a regular log file opened for append, None if the path is not one."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags, 0o600)
    if stat.S_ISREG(os.fstat(fd).st_mode):
        return fd
    os.close(fd)
    return None


def install(settings, command, replace=False):
    """Set statusLine to the command in a Claude Code settings file, keeping other keys."""
    wanted = {'type': 'command', 'command': command}
    if not os.path.exists(settings):
        write_settings(settings, {'statusLine': wanted})
        return 'ajouté'
    data = read_settings(settings)
    if data.get('statusLine') == wanted:
        return 'identique'
    if 'statusLine' in data and not replace:
        return 'conservé'
    status = 'remplacé' if 'statusLine' in data else 'ajouté'
    data['statusLine'] = wanted
    write_settings(settings, data)
    return status


def read_settings(settings):
    try:
        with open(settings, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError('réglages illisibles : ' + str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError('réglages illisibles : objet JSON attendu')
    return data


def write_settings(settings, data):
    """Replace the settings file atomically, keeping its permissions (0644 for a new file)."""
    folder = os.path.dirname(os.path.abspath(settings))
    os.makedirs(folder, exist_ok=True)
    mode = stat.S_IMODE(os.stat(settings).st_mode) if os.path.exists(settings) else 0o644
    fd, temp = tempfile.mkstemp(dir=folder, prefix='.statusline-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.chmod(temp, mode)
        os.replace(temp, settings)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


if __name__ == '__main__':
    sys.exit(main())
