"""Read-only dashboard for meshtripwire.

Serves one static page plus two JSON endpoints straight from the events table
that the monitor writes. Stdlib only — no framework, no Internet, works on the
same off-grid Pi as the rest of the stack.

    python -m dashboard.server [--port 8080] [--db logs/detections.db]

Endpoints:
    /                  the dashboard page
    /api/events?limit         recent events, newest first (default 300)
    /api/nodes                per-node last-seen and event count
    /api/notifications?limit&offset  alert delivery attempts per channel (default 100)
    /history                  full-history search page
    /api/search?q&type&node&event&from&to&limit&offset   filtered event search
    /api/facets               distinct types/nodes/events for the filters
    /api/outbox               alert-delivery backlog: counts + oldest pending
    /api/status               monitor liveness, broker link, stale threshold, missing sensors
Read-only by design: arming and configuration stay on the MQTT control topic
and config.ini, so the dashboard adds no attack surface beyond a status page.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_LIMIT = 2000  # cap page size; SQLite treats a negative LIMIT as unbounded


def _bound(val, default, hi=MAX_LIMIT, lo=1):
    """User-supplied count clamped to [lo, hi]; junk or sub-lo values fall back to
    default (so a negative LIMIT never becomes SQLite's 'unbounded')."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return default if n < lo else min(n, hi)


def query_nodes(conn):
    """One row per node: last-seen and event count, merging event history with
    node_health so a heartbeat-only node (present but quiet) still appears."""
    nodes = {}
    try:
        for n, ts, c in conn.execute(
                "SELECT node, MAX(ts), COUNT(*) FROM events GROUP BY node"):
            nodes[n] = {'node': n, 'last_seen': ts, 'events': c, 'source': 'event'}
    except sqlite3.OperationalError:
        return []  # monitor hasn't created the events table yet
    try:
        for n, ts, src in conn.execute("SELECT node, last_seen, source FROM node_health"):
            row = nodes.setdefault(n, {'node': n, 'last_seen': ts, 'events': 0, 'source': src})
            if ts and ts > (row['last_seen'] or ''):  # health beat newer than last event
                row['last_seen'] = ts
                row['source'] = src
    except sqlite3.OperationalError:
        pass  # older DB without node_health
    return sorted(nodes.values(), key=lambda r: r['last_seen'] or '', reverse=True)


def query_status(conn):
    """Monitor liveness for the dashboard: whether the monitor is writing, its
    broker link, the configured stale threshold, and which expected sensors are
    currently missing. Lets a fetch distinguish monitor-down from empty history."""
    st = {'monitor_seen': None, 'broker_connected': False, 'sensor_timeout': 900,
          'expected': [], 'missing': []}
    try:
        row = conn.execute("SELECT ts, broker_connected, sensor_timeout, expected, armed "
                           "FROM monitor_status WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return st
    if not row:
        return st
    st['monitor_seen'] = row[0]
    st['broker_connected'] = bool(row[1])
    st['sensor_timeout'] = row[2] or 900
    st['armed'] = bool(row[4])
    expected = [s.strip() for s in (row[3] or '').split(',') if s.strip()]
    st['expected'] = expected
    if expected:
        seen = {n: ts for n, ts, _ in
                conn.execute("SELECT node, last_seen, source FROM node_health")}
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=st['sensor_timeout'])).isoformat()
        st['missing'] = [n for n in expected if seen.get(n, '') < cutoff]
    return st


def query_outbox(conn):
    """Alert-delivery backlog: counts by status and the oldest pending timestamp."""
    out = {'pending': 0, 'dead': 0, 'sent': 0, 'oldest_pending': None}
    try:
        for status, n in conn.execute(
                "SELECT status, COUNT(*) FROM alert_outbox GROUP BY status"):
            out[status] = n
        out['oldest_pending'] = conn.execute(
            "SELECT MIN(ts) FROM alert_outbox WHERE status='pending'").fetchone()[0]
    except sqlite3.OperationalError:
        pass  # table not created yet
    return out


def query_notifications(conn, limit=100, offset=0):
    """Notification attempts, newest first, with offset paging for 'load older'."""
    try:
        rows = conn.execute(
            "SELECT ts, channel, target, ok, error, message FROM notifications "
            "ORDER BY ts DESC LIMIT ? OFFSET ?", (_bound(limit, 100), _bound(offset, 0, lo=0))).fetchall()
    except sqlite3.OperationalError:
        return []  # monitor hasn't created the notifications table yet
    return [{'ts': ts, 'channel': ch, 'target': tg, 'ok': ok, 'error': err,
             'message': msg} for ts, ch, tg, ok, err, msg in rows]


def query_search(conn, q='', type_='', node='', event='', tfrom='', tto='',
                 limit=100, offset=0):
    """Filtered event search, newest first. Empty filters mean 'match all'."""
    sql = ("SELECT ts, node, type, sensor, event, value, meta FROM events "
           "WHERE (? = '' OR type = ?) AND (? = '' OR node = ?) "
           "AND (? = '' OR event = ?) AND (? = '' OR ts >= ?) AND (? = '' OR ts <= ?) "
           "AND (? = '' OR node LIKE ? OR event LIKE ? OR meta LIKE ?) "
           "ORDER BY ts DESC LIMIT ? OFFSET ?")
    like = f'%{q}%'
    try:
        rows = conn.execute(sql, (type_, type_, node, node, event, event,
                                  tfrom, tfrom, tto, tto,
                                  q, like, like, like,
                                  _bound(limit, 100), _bound(offset, 0, lo=0))).fetchall()
    except sqlite3.OperationalError:
        return []  # monitor hasn't created the events table yet
    events = []
    for ts, node_, t, sensor, ev, value, meta in rows:
        try:
            meta = json.loads(meta) if meta else {}
        except ValueError:
            meta = {}
        events.append({'ts': ts, 'node': node_, 'type': t, 'sensor': sensor,
                       'event': ev, 'value': value, 'meta': meta})
    return events


def query_facets(conn):
    """Distinct types/nodes/events, for the search page's filter dropdowns."""
    try:
        return {
            'types': [r[0] for r in conn.execute(
                "SELECT DISTINCT type FROM events ORDER BY type") if r[0]],
            'nodes': [r[0] for r in conn.execute(
                "SELECT DISTINCT node FROM events ORDER BY node") if r[0]],
            'events': [r[0] for r in conn.execute(
                "SELECT DISTINCT event FROM events ORDER BY event") if r[0]],
        }
    except sqlite3.OperationalError:
        return {'types': [], 'nodes': [], 'events': []}


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            # Per-request read-only connection: cheap at dashboard rates, and
            # never contends with the monitor's writer connection.
            if url.path.startswith('/api/'):
                try:
                    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
                except sqlite3.OperationalError:
                    self._json({'error': 'database not found', 'events': [], 'nodes': []})
                    return
                try:
                    if url.path == '/api/events':
                        limit = parse_qs(url.query).get('limit', ['300'])[0]
                        self._json(query_search(conn, limit=limit))
                    elif url.path == '/api/nodes':
                        self._json(query_nodes(conn))
                    elif url.path == '/api/notifications':
                        q = parse_qs(url.query)
                        self._json(query_notifications(
                            conn, q.get('limit', ['100'])[0], q.get('offset', ['0'])[0]))
                    elif url.path == '/api/search':
                        p = {k: v[0] for k, v in parse_qs(url.query).items()}
                        self._json(query_search(
                            conn, q=p.get('q', ''), type_=p.get('type', ''),
                            node=p.get('node', ''), event=p.get('event', ''),
                            tfrom=p.get('from', ''), tto=p.get('to', ''),
                            limit=p.get('limit', 100), offset=p.get('offset', 0)))
                    elif url.path == '/api/facets':
                        self._json(query_facets(conn))
                    elif url.path == '/api/outbox':
                        self._json(query_outbox(conn))
                    elif url.path == '/api/status':
                        self._json(query_status(conn))
                    else:
                        self.send_error(404)
                finally:
                    conn.close()
            elif url.path in ('/', '/history'):
                page = 'history.html' if url.path == '/history' else 'index.html'
                with open(os.path.join(STATIC_DIR, page), 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # quiet; this serves one LAN client

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--db', default='logs/detections.db')
    args = ap.parse_args()
    server = ThreadingHTTPServer(('0.0.0.0', args.port), make_handler(args.db))
    print(f"Dashboard on http://0.0.0.0:{args.port} (db: {args.db})")
    server.serve_forever()


if __name__ == '__main__':
    main()
