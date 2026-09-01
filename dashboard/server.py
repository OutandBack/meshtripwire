"""Read-only dashboard for meshtripwire.

Serves one static page plus two JSON endpoints straight from the events table
that the monitor writes. Stdlib only — no framework, no Internet, works on the
same off-grid Pi as the rest of the stack.

    python -m dashboard.server [--port 8080] [--db logs/detections.db]

Endpoints:
    /                  the dashboard page
    /api/events?limit         recent events, newest first (default 300)
    /api/nodes                per-node last-seen and event count
    /api/notifications?limit  alert delivery attempts per channel (default 100)
    /history                  full-history search page
    /api/search?q&type&node&event&from&to&limit&offset   filtered event search
    /api/facets               distinct types/nodes/events for the filters
Read-only by design: arming and configuration stay on the MQTT control topic
and config.ini, so the dashboard adds no attack surface beyond a status page.
"""
import argparse
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATIC_DIR = os.path.dirname(os.path.abspath(__file__))


def query_nodes(conn):
    """One row per node: last event timestamp and total event count."""
    try:
        rows = conn.execute(
            "SELECT node, MAX(ts), COUNT(*) FROM events GROUP BY node "
            "ORDER BY MAX(ts) DESC").fetchall()
    except sqlite3.OperationalError:
        return []  # monitor hasn't created the events table yet
    return [{'node': n, 'last_seen': ts, 'events': c} for n, ts, c in rows]


def query_notifications(conn, limit=100):
    """Recent notification attempts, newest first."""
    try:
        rows = conn.execute(
            "SELECT ts, channel, target, ok, error, message FROM notifications "
            "ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
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
                                  int(limit), int(offset))).fetchall()
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
                        limit = parse_qs(url.query).get('limit', ['100'])[0]
                        self._json(query_notifications(conn, limit))
                    elif url.path == '/api/search':
                        p = {k: v[0] for k, v in parse_qs(url.query).items()}
                        self._json(query_search(
                            conn, q=p.get('q', ''), type_=p.get('type', ''),
                            node=p.get('node', ''), event=p.get('event', ''),
                            tfrom=p.get('from', ''), tto=p.get('to', ''),
                            limit=p.get('limit', 100), offset=p.get('offset', 0)))
                    elif url.path == '/api/facets':
                        self._json(query_facets(conn))
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
