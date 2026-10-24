"""Line parsers for the heterogeneous log formats found in dataset/evidence/logs."""
import json
import re

TS_RE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$')
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|[^\s]*)')
BSLASH = chr(92)


def _unq(v):
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace(BSLASH + '"', '"')
    return v


def kv(text):
    return {k: _unq(v) for k, v in KV_RE.findall(text)}


def parse_line(line):
    """Return dict with ts, service, trace, event, f(fields) -- or None."""
    line = line.rstrip('\n')
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2)
    rec = {'ts': ts, 'service': None, 'trace': None, 'event': None, 'f': {}}
    if rest.startswith('{'):  # structured json
        try:
            d = json.loads(rest)
        except ValueError:
            return None
        rec['service'] = d.get('service') or d.get('logger')
        rec['trace'] = d.get('trace_id')
        rec['event'] = d.get('event')
        rec['f'] = {k: str(v) for k, v in d.items()}
    elif rest.startswith('<'):  # syslog
        m2 = re.match(r'<\d+>1\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$', rest)
        if not m2:
            return None
        rec['service'], rec['trace'] = m2.group(2), m2.group(3)
        rec['f'] = kv(m2.group(4))
        rec['event'] = rec['f'].get('event')
    elif rest.startswith('['):  # application trace
        m2 = re.match(r'\[(\w+)\]\s+\[(\w+)\]\s+\[([^\]]*)\]\s+([A-Za-z0-9_]+):\s*(.*)$', rest)
        if not m2:
            return None
        rec['service'], rec['trace'], rec['event'] = m2.group(2), m2.group(3), m2.group(4)
        rec['f'] = kv(m2.group(5))
    elif rest.startswith('requestId='):  # cloud runtime export
        m2 = re.match(r'requestId=(\S+)\s+level=(\S+)\s+function=(\S+)\s+message="(.*)"\s*$', rest)
        if not m2:
            return None
        rec['trace'], rec['service'] = m2.group(1), m2.group(3)
        rec['f'] = kv(m2.group(4).replace(BSLASH + '"', '"'))
        rec['event'] = rec['f'].get('event')
    elif ' | ' in rest[:40]:  # operations stream
        parts = [p.strip() for p in rest.split(' | ')]
        if len(parts) < 5:
            return None
        rec['service'], rec['trace'], rec['event'] = parts[1], parts[2], parts[3]
        rec['f'] = kv(' '.join(parts[4:]))
    else:  # plain key=value
        f = kv(rest)
        rec['service'] = f.get('service') or f.get('logger')
        rec['trace'] = f.get('trace_id') or f.get('trace')
        rec['event'] = f.get('event')
        rec['f'] = f
    return rec
