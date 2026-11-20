#!/usr/bin/env python3
"""Official metadata -> conservative local matching -> UTF-8 M3U8. Python 3.10+."""
import argparse
import csv
import difflib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import urllib.error
import urllib.parse as url
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

AUDIO = {'.mp3', '.wav', '.aif', '.aiff', '.flac', '.m4a', '.aac', '.alac'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    with Path(path).open('x', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def norm(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def artists(value):
    return [str(v).strip() for v in (value if isinstance(value, list) else str(value or '').split(';')) if str(v).strip()]


def artist_key(value):
    return tuple(sorted(norm(v) for v in artists(value)))


def duration(value):
    if value in (None, ''):
        return None
    n = float(value)
    if not 0 < n < 86400000:
        raise ValueError('Invalid duration_ms')
    return round(n)


def safe_path(raw):
    raw = str(raw)
    if any(ord(c) < 32 for c in raw):
        raise ValueError('Control character in local path')
    if not Path(raw).is_absolute():
        raise ValueError('Local audio paths must be absolute')
    # Preserve the exact library path (junctions, symlinks, Windows short names).
    # Rekordbox may identify existing collection entries by that path.
    p = Path(raw)
    if p.suffix.lower() not in AUDIO or not p.is_file() or p.stat().st_size == 0:
        raise ValueError('Missing, empty, or unsupported local audio file')
    return p


def source_url(raw):
    u = url.urlsplit(raw)
    if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Use an official HTTPS playlist URL')
    if u.hostname == 'open.spotify.com':
        m = re.fullmatch(r'/(?:intl-[a-z-]+/)?playlist/([A-Za-z0-9]+)', u.path.rstrip('/'))
        if m:
            return 'spotify', m[1], 'https://open.spotify.com/playlist/' + m[1]
    if u.hostname in ('soundcloud.com', 'www.soundcloud.com') and re.fullmatch(r'/[^/]+/sets/[^/]+', u.path.rstrip('/')):
        # Private secret tokens are intentionally not copied into artifacts.
        return 'soundcloud', None, 'https://soundcloud.com' + u.path.rstrip('/')
    raise ValueError('Open a shortened share link in the official site and use its full playlist URL')


def check_api(raw, host):
    u = url.urlsplit(raw)
    if u.scheme != 'https' or u.hostname != host or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Refused non-official API destination')
    if host == 'api.spotify.com':
        ok = re.fullmatch(r'/v1/playlists/[A-Za-z0-9]+(?:/items)?', u.path)
    else:
        ok = re.fullmatch(r'/(?:resolve|playlists/[^/]+(?:/tracks)?|tracks/[^/]+)', u.path)
    if not ok:
        raise ValueError('Refused endpoint outside metadata operations')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_api(newurl, self.host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def api_get(raw, host, token):
    check_api(raw, host)
    scheme = 'Bearer' if host == 'api.spotify.com' else 'OAuth'
    req = urllib.request.Request(raw, headers={'Authorization': scheme + ' ' + token, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(SafeRedirect(host)).open(req, timeout=30) as response:
            payload = response.read(10_000_001)
        if len(payload) > 10_000_000:
            raise ValueError('API response too large')
        return json.loads(payload)
    except urllib.error.HTTPError as e:
        # Do not log URLs, bodies, headers, or credentials.
        raise ValueError(f'Official API HTTP {e.code}; check authorization/rate limit; no bypass attempted') from None
    except urllib.error.URLError:
        raise ValueError('Official API connection failed; no partial output written') from None


def spotify_track(item, position):
    t = item.get('item', item.get('track')) if isinstance(item, dict) else None
    base = {'position': position, 'title': '', 'artists': [], 'status': 'unavailable'}
    if not t:
        return base
    base.update(title=t.get('name', ''), artists=[a.get('name', '') for a in t.get('artists', [])],
                duration_ms=t.get('duration_ms'), isrc=t.get('external_ids', {}).get('isrc', ''),
                source_id=t.get('id', ''), source_url=t.get('external_urls', {}).get('spotify', ''))
    base['status'] = 'ready' if t.get('type', 'track') == 'track' else 'unsupported_type'
    base['is_local'] = bool(item.get('is_local') or t.get('is_local'))
    return base


def sc_track(t, position):
    if not t or not t.get('title'):
        return {'position': position, 'title': '', 'artists': [], 'status': 'unavailable', 'source_id': str((t or {}).get('id', ''))}
    publisher = t.get('publisher_metadata') or {}
    artist = t.get('metadata_artist') or publisher.get('artist') or ''
    return {'position': position, 'title': t['title'], 'artists': artists(artist),
            'uploader': (t.get('user') or {}).get('username', ''),
            'duration_ms': t.get('duration'), 'isrc': publisher.get('isrc', ''),
            'source_id': str(t.get('id', '')), 'source_url': t.get('permalink_url', ''),
            'status': 'ready', 'official_download_hint': t.get('downloadable') is True}


def fetch_playlist(raw, token, getter=api_get):
    service, identifier, canonical = source_url(raw)
    host = 'api.spotify.com' if service == 'spotify' else 'api.soundcloud.com'
    get = lambda u: getter(u, host, token)
    tracks = []
    if service == 'spotify':
        root = f'https://{host}/v1/playlists/{identifier}'
        metadata = get(root)
        next_page = root + '/items?limit=50'
        seen = set()
        total = None
        while next_page:
            check_api(next_page, host)
            if next_page in seen or len(seen) >= 10000:
                raise ValueError('Invalid or excessive pagination')
            seen.add(next_page)
            page = get(next_page)
            if page.get('offset') != len(tracks) or not isinstance(page.get('items'), list):
                raise ValueError('Playlist page offset/schema mismatch')
            if total is not None and page.get('total') != total:
                raise ValueError('Playlist changed during extraction')
            total = page.get('total')
            for item in page['items']:
                tracks.append(spotify_track(item, len(tracks) + 1))
            next_page = page.get('next')
        after = get(root)
        if not metadata.get('snapshot_id') or metadata.get('snapshot_id') != after.get('snapshot_id'):
            raise ValueError('Playlist snapshot unavailable or changed; retry extraction')
        if total != len(tracks):
            raise ValueError('Playlist count mismatch; extraction incomplete')
        name = metadata.get('name', 'Spotify playlist')
    else:
        # Only official API, never api-v2/client_id scraping or playback URLs.
        metadata = get('https://api.soundcloud.com/resolve?' + url.urlencode({'url': raw}))
        if metadata.get('kind') != 'playlist':
            raise ValueError('URL did not resolve to a SoundCloud playlist')
        total = metadata.get('track_count')
        entries = metadata.get('tracks')
        if not isinstance(entries, list) or total != len(entries):
            raise ValueError('SoundCloud did not provide the full ordered track list; use verified metadata input')
        for t in entries:
            if t and not t.get('title') and t.get('id'):
                try:
                    t = get('https://api.soundcloud.com/tracks/' + url.quote(str(t['id']), safe=''))
                except ValueError as exc:
                    if 'HTTP 404' not in str(exc):
                        raise
            tracks.append(sc_track(t, len(tracks) + 1))
        # Re-read IDs to detect edits while individual track details were resolved.
        after = get('https://api.soundcloud.com/resolve?' + url.urlencode({'url': raw}))
        if [str((t or {}).get('id')) for t in after.get('tracks', [])] != [str((t or {}).get('id')) for t in entries]:
            raise ValueError('SoundCloud playlist changed during extraction')
        name = metadata.get('title', 'SoundCloud playlist')
    return {'schema_version': 1, 'name': name, 'source_url': canonical, 'service': service,
            'complete': True, 'expected_count': total, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'tracks': tracks}


def validate_playlist(data):
    if data.get('schema_version') != 1 or not isinstance(data.get('complete'), bool):
        raise ValueError('Playlist needs schema_version=1 and boolean complete')
    source_url(data['source_url'])
    tracks = data.get('tracks')
    if not isinstance(tracks, list):
        raise ValueError('tracks must be an ordered array')
    positions = [t.get('position') for t in tracks]
    if any(type(p) is not int or p < 1 for p in positions) or positions != sorted(set(positions)):
        raise ValueError('Positions must be unique increasing positive integers')
    if data['complete'] and (data.get('expected_count') != len(tracks) or positions != list(range(1, len(tracks) + 1))):
        raise ValueError('Complete playlist requires contiguous positions and verified count')
    for t in tracks:
        duration(t.get('duration_ms'))
        if t.get('status', 'ready') not in ('ready', 'unavailable', 'unsupported_type'):
            raise ValueError('Unknown track status')
    return data


def xml_local_path(location):
    u = url.urlsplit(location)
    if u.scheme != 'file' or u.netloc not in ('', 'localhost'):
        raise ValueError('Not a local file URI (remote/UNC locations require explicit local mapping)')
    raw = url.unquote(u.path)  # exactly once: literal %20 stays literal if encoded as %2520
    if re.match(r'^/[A-Za-z]:/', raw):
        raw = raw[1:]
    return raw


def read_library(xml_path):
    data = Path(xml_path).read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML DTD/entities are not supported')
    root = ET.fromstring(data)
    if root.tag != 'DJ_PLAYLISTS' or root.find('COLLECTION') is None:
        raise ValueError('Not a rekordbox collection XML')
    library, warnings = [], []
    for el in root.findall('./COLLECTION/TRACK'):
        a = el.attrib
        try:
            raw = xml_local_path(a.get('Location', ''))
            p = safe_path(raw)
        except (ValueError, OSError):
            warnings.append({'track_id': a.get('TrackID', ''), 'title': a.get('Name', ''), 'reason': 'missing_or_nonlocal_audio'})
            continue
        seconds = a.get('TotalTime')
        library.append({'path': str(p), 'title': a.get('Name', ''), 'artists': artists(a.get('Artist', '')),
                        'duration_ms': float(seconds) * 1000 if seconds and float(seconds) > 0 else None,
                        'isrc': a.get('ISRC', ''), 'origin': 'rekordbox', 'track_id': a.get('TrackID', '')})
    return library, warnings


def scan_folder(folder):
    try:
        from mutagen import File
    except ImportError:
        raise ValueError('Folder scanning needs mutagen; use rekordbox XML or --local-index instead') from None
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError('Audio folder does not exist')
    result, warnings = [], []
    for p in sorted(folder.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in AUDIO:
            continue
        try:
            audio = File(p, easy=True)
            if audio is None or not audio.tags:
                raise ValueError('No usable tags')
            tags = audio.tags
            first = lambda key: (tags.get(key) or [''])[0]
            result.append({'path': str(safe_path(p.resolve())), 'title': first('title'),
                           'artists': artists(tags.get('artist', [])), 'duration_ms': round(audio.info.length * 1000),
                           'isrc': first('isrc'), 'origin': 'local'})
        except Exception:
            warnings.append({'path': str(p), 'reason': 'tag_read_failed_use_local_index'})
    return result, warnings


def normalize_library(items):
    by_path = {}
    for item in items:
        item = dict(item)
        item['path'] = str(safe_path(item['path']))
        item['duration_ms'] = duration(item.get('duration_ms'))
        item['artists'] = artists(item.get('artists'))
        k = os.path.normcase(str(Path(item['path']).resolve()))
        if k not in by_path or item.get('origin') == 'rekordbox':
            by_path[k] = item
    return list(by_path.values())


def duration_ok(track, local):
    a, b = duration(track.get('duration_ms')), duration(local.get('duration_ms'))
    return a is None or b is None or abs(a - b) <= 3000


def choose(track, library):
    if track.get('status', 'ready') != 'ready':
        return None, track['status'], []
    title = norm(track.get('title'))
    ak = artist_key(track.get('artists'))
    exact = []
    for local in library:
        if not title or title != norm(local.get('title')) or not duration_ok(track, local):
            continue
        isrc_match = norm(track.get('isrc')) and norm(track['isrc']) == norm(local.get('isrc'))
        if isrc_match or (ak and ak == artist_key(local.get('artists'))):
            exact.append(local)
    preferred = [c for c in exact if c.get('origin') == 'rekordbox'] or exact
    if len(preferred) == 1:
        return preferred[0], 'exact_metadata', []
    if len(preferred) > 1:
        return None, 'ambiguous', preferred
    suggestions = []
    for local in library:
        similarity = difflib.SequenceMatcher(None, title, norm(local.get('title'))).ratio()
        if title and similarity >= .7 and (not ak or ak == artist_key(local.get('artists'))):
            suggestions.append((similarity, local))
    suggestions.sort(key=lambda c: (-c[0], c[1]['path']))
    return None, 'no_confident_match', [v for _, v in suggestions[:5]]


def csv_cell(value):
    value = str(value if value is not None else '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def build(data, library, out, overrides=None, warnings=None):
    validate_playlist(data)
    library = normalize_library(library)
    overrides = overrides or {}
    known = {str(t['position']) for t in data['tracks']}
    if not set(overrides).issubset(known):
        raise ValueError('Override references an unknown source position')
    rows = []
    for track in data['tracks']:
        local, reason, candidates = choose(track, library)
        approval = overrides.get(str(track['position']))
        if approval:
            if approval.get('confirmed') is not True or not approval.get('evidence'):
                raise ValueError('Override requires confirmed=true and evidence')
            local = {'path': str(safe_path(approval['path'])), 'origin': 'confirmed', 'duration_ms': None}
            reason = 'explicitly_confirmed'
            candidates = []
        rows.append({**track, 'matched': local is not None, 'reason': reason,
                     'path': local['path'] if local else None, 'origin': local.get('origin') if local else None,
                     'duration_checked': bool(local and track.get('duration_ms') and local.get('duration_ms')),
                     'candidates': candidates, 'acquisition': approval if approval else None})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    # Never overwrite prior outputs. No copying, editing or moving audio.
    with (out / 'playlist.m3u8').open('x', encoding='utf-8', newline='\n') as f:
        f.write('#EXTM3U\n')
        for row in rows:
            if row['matched']:
                label = ' / '.join(artists(row.get('artists'))) + ' - ' + row.get('title', '')
                label = ''.join(' ' if ord(c) < 32 else c for c in label)
                seconds = round(duration(row.get('duration_ms')) / 1000) if row.get('duration_ms') else -1
                f.write(f'#EXTINF:{seconds},{label}\n{row["path"]}\n')
    report = {'schema_version': 1, 'name': data.get('name', ''), 'source_url': data['source_url'],
              'complete': data['complete'], 'expected_count': data.get('expected_count'),
              'extracted_count': len(rows), 'matched_count': sum(r['matched'] for r in rows),
              'unmatched_count': sum(not r['matched'] for r in rows), 'warnings': warnings or [], 'tracks': rows}
    write_json(out / 'report.json', report)
    with (out / 'unmatched.csv').open('x', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['position', 'title', 'artists', 'reason', 'source_url', 'candidate_paths'])
        for row in rows:
            if not row['matched']:
                writer.writerow([csv_cell(v) for v in (row['position'], row.get('title'), '; '.join(artists(row.get('artists'))),
                                                       row['reason'], row.get('source_url', ''), ' | '.join(c['path'] for c in row['candidates']))])
    instructions = Path(__file__).resolve().parent.parent / 'references' / 'import-template.md'
    text = instructions.read_text(encoding='utf-8')
    header = f'抽出 {len(rows)}曲 / 一致 {report["matched_count"]}曲 / 未一致 {report["unmatched_count"]}曲\n\n'
    if not data['complete']:
        header += '注意: 元プレイリストの全件取得は未確認です。これは部分再現です。\n\n'
    (out / 'IMPORT.md').write_text(header + text, encoding='utf-8')
    verify(out)
    return report


def verify(out):
    out = Path(out)
    report = read_json(out / 'report.json')
    paths = [line for line in (out / 'playlist.m3u8').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]
    expected = [r['path'] for r in report['tracks'] if r['matched']]
    if paths != expected or len(paths) != report['matched_count']:
        raise ValueError('Playlist and report order/count mismatch')
    if len(report['tracks']) != report['extracted_count'] or report['extracted_count'] != report['matched_count'] + report['unmatched_count']:
        raise ValueError('Report count mismatch')
    for p in paths:
        safe_path(p)
    return len(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('fetch', help='Fetch official playlist metadata; never downloads audio')
    fetch.add_argument('--url', required=True)
    fetch.add_argument('--output', required=True)
    b = commands.add_parser('build', help='Match local audio and create a new output folder')
    b.add_argument('--playlist', required=True)
    b.add_argument('--rekordbox-xml', action='append', default=[])
    b.add_argument('--audio-dir', action='append', default=[])
    b.add_argument('--local-index', action='append', default=[])
    b.add_argument('--overrides')
    b.add_argument('--out', required=True)
    v = commands.add_parser('verify')
    v.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'fetch':
            service, _, _ = source_url(args.url)
            key = 'SPOTIFY_ACCESS_TOKEN' if service == 'spotify' else 'SOUNDCLOUD_ACCESS_TOKEN'
            token = os.environ.get(key)
            if not token:
                raise ValueError('Set ' + key + ' privately, or supply a verified playlist JSON; do not paste tokens into chat')
            if Path(args.output).exists():
                raise ValueError('Output exists; choose a new metadata file')
            data = validate_playlist(fetch_playlist(args.url, token))
            write_json(args.output, data)
            print(f'Metadata saved: {len(data["tracks"])} positions. No audio downloaded.')
        elif args.command == 'build':
            library, warnings = [], []
            for xml in args.rekordbox_xml:
                tracks, notes = read_library(xml)
                library.extend(tracks)
                warnings.extend(notes)
            for folder in args.audio_dir:
                tracks, notes = scan_folder(folder)
                library.extend(tracks)
                warnings.extend(notes)
            for index in args.local_index:
                for item in read_json(index):
                    library.append({**item, 'origin': 'local'})
            report = build(read_json(args.playlist), library, args.out,
                           read_json(args.overrides) if args.overrides else None, warnings)
            print(f'Matched {report["matched_count"]}; unmatched {report["unmatched_count"]}; complete={report["complete"]}')
        else:
            print(f'Verified {verify(args.out)} local entries (including duplicates).')
        return 0
    except (ValueError, OSError, ET.ParseError, KeyError, TypeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
