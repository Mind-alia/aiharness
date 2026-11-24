"""Bounded command capture; reuse the project's secret policy before display."""

import os
import selectors
import subprocess
from secrets import known_values, mask_secrets

LIMIT = 2 * 1024 * 1024


def safe_text(raw, values, truncated=False):
    text = raw.decode('utf-8', errors='replace')
    # A cut token cannot be matched reliably. Discard the incomplete last line.
    if truncated:
        text = text.rpartition('\n')[0] + '\n[TRUNCATED]\n'
    return mask_secrets(text, values)


def run_masked(command, root):
    values = known_values(root)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    with selectors.DefaultSelector() as selector:
        for i, stream in enumerate((process.stdout, process.stderr)):
            selector.register(stream, selectors.EVENT_READ, i)
        while selector.get_map():
            for key, _ in selector.select():
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                i = key.data
                available = LIMIT - len(buffers[i])
                buffers[i].extend(block[:available])
                truncated[i] |= len(block) > available
    code = process.wait()
    values = sorted(set(values) | set(known_values(root)))
    return (code if code >= 0 else 128 - code, *(safe_text(bytes(raw), values, cut) for raw, cut in zip(buffers, truncated, strict=False)))
