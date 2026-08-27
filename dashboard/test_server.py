"""Check the dashboard queries. Run: venv/bin/python -m dashboard.test_server"""
import sqlite3

from dashboard.server import query_events, query_nodes, query_notifications

conn = sqlite3.connect(':memory:')
conn.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, node TEXT, type TEXT, sensor TEXT,
    event TEXT, value REAL, lat REAL, lon REAL, meta TEXT)""")
rows = [
    ('2026-08-27T10:00:00+00:00', 'gate', 'vehicle', 'qmc5883l', 'detected', 73.0, '{}'),
    ('2026-08-27T10:00:30+00:00', 'fence-e', 'vibration', 'piezo', 'shake', 9.0, '{}'),
    ('2026-08-27T10:05:00+00:00', 'gate', 'wireless_presence', None, 'detected', None,
     '{"mac": "DE:AD:BE:EF:00:01", "rssi": -61, "status": "unknown"}'),
]
conn.executemany("INSERT INTO events (ts, node, type, sensor, event, value, meta) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

# Events: newest first, meta decoded, limit respected
evs = query_events(conn, limit=2)
assert len(evs) == 2 and evs[0]['type'] == 'wireless_presence', evs
assert evs[0]['meta']['mac'] == 'DE:AD:BE:EF:00:01'
assert evs[1] == {'ts': '2026-08-27T10:00:30+00:00', 'node': 'fence-e',
                  'type': 'vibration', 'sensor': 'piezo', 'event': 'shake',
                  'value': 9.0, 'meta': {}}, evs[1]

# Nodes: one row per node with last-seen and total event count
nodes = {n['node']: n for n in query_nodes(conn)}
assert nodes['gate']['last_seen'] == '2026-08-27T10:05:00+00:00'
assert nodes['gate']['events'] == 2 and nodes['fence-e']['events'] == 1

# Notification log: newest first, ok as boolean-ish int, limit respected
conn.execute("""CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, channel TEXT, target TEXT, ok INTEGER, error TEXT, message TEXT)""")
conn.executemany("INSERT INTO notifications (ts, channel, target, ok, error, message) VALUES (?,?,?,?,?,?)", [
    ('2026-08-27T10:00:00+00:00', 'ntfy', 'meshtripwire-abc', 1, None, 'ALERT: Vehicle detected'),
    ('2026-08-27T10:00:01+00:00', 'twilio', '+1987654321', 0, 'timeout', 'ALERT: Vehicle detected'),
])
ns = query_notifications(conn, limit=1)
assert ns == [{'ts': '2026-08-27T10:00:01+00:00', 'channel': 'twilio',
               'target': '+1987654321', 'ok': 0, 'error': 'timeout',
               'message': 'ALERT: Vehicle detected'}], ns

# Database without an events table yet (monitor never ran): empty, no errors
bare = sqlite3.connect(':memory:')
assert query_events(bare) == [] and query_nodes(bare) == []
assert query_notifications(bare) == []

# Empty database (fresh install): both return empty lists, no errors
empty = sqlite3.connect(':memory:')
empty.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, node TEXT, "
              "type TEXT, sensor TEXT, event TEXT, value REAL, lat REAL, lon REAL, meta TEXT)")
assert query_events(empty) == [] and query_nodes(empty) == []

print('dashboard test OK')
