#!/usr/bin/env python3
"""Offline regression tests; no accounts, downloads, or user's library touched."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from unittest.mock import patch
import playlist_rebuild as p


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.a = self.audio('日本語 # 100%20.wav')
        self.b = self.audio('copy.wav')
        self.local = {'path': str(self.a), 'title': '夜 (Extended Mix)', 'artists': ['DJ A'],
                      'duration_ms': 120000, 'origin': 'rekordbox'}
        self.track = {'position': 1, 'title': self.local['title'], 'artists': ['DJ A'], 'duration_ms': 120000}

    def tearDown(self):
        self.temp.cleanup()

    def audio(self, name):
        path = self.root / name
        with wave.open(str(path), 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(8000)
            f.writeframes(b'\x00\x00' * 800)
        return path

    def playlist(self, tracks=None, complete=True):
        tracks = tracks if tracks is not None else [self.track]
        return {'schema_version': 1, 'name': '検証', 'source_url': 'https://open.spotify.com/playlist/EXAMPLE',
                'complete': complete, 'expected_count': len(tracks) if complete else None, 'tracks': tracks}

    def test_order_duplicates_and_unicode_paths(self):
        tracks = [self.track, {**self.track, 'position': 2, 'title': 'missing'}, {**self.track, 'position': 3}]
        out = self.root / 'out'
        report = p.build(self.playlist(tracks), [self.local], out)
        self.assertEqual((report['matched_count'], report['unmatched_count']), (2, 1))
        self.assertEqual(p.verify(out), 2)
        paths = [s for s in (out / 'playlist.m3u8').read_text(encoding='utf-8').splitlines() if not s.startswith('#')]
        self.assertEqual(paths, [str(self.a), str(self.a)])
        self.assertEqual([r['position'] for r in report['tracks']], [1, 2, 3])

    def test_existing_rekordbox_preferred_over_local(self):
        other = {**self.local, 'path': str(self.b), 'origin': 'local'}
        match, _, _ = p.choose(self.track, [other, self.local])
        self.assertEqual(match['path'], str(self.a))

    def test_two_existing_candidates_are_ambiguous(self):
        other = {**self.local, 'path': str(self.b)}
        match, reason, candidates = p.choose(self.track, [other, self.local])
        self.assertIsNone(match)
        self.assertEqual(reason, 'ambiguous')
        self.assertEqual(len(candidates), 2)

    def test_mix_version_and_duration_not_silently_substituted(self):
        for replacement in ({'title': '夜 (Radio Edit)'}, {'duration_ms': 200000}, {'artists': ['DJ B']}):
            with self.subTest(replacement=replacement):
                self.assertIsNone(p.choose(self.track, [{**self.local, **replacement}])[0])

    def test_normalization_keeps_japanese(self):
        track = {**self.track, 'title': '  夜 (EXTENDED Mix) ', 'artists': ['ＤＪ A']}
        self.assertIsNotNone(p.choose(track, [self.local])[0])

    def test_same_path_deduplicated_existing_metadata_wins(self):
        items = p.normalize_library([{**self.local, 'origin': 'local', 'title': 'wrong'}, self.local])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], self.local['title'])

    def test_xml_percent_decoded_once_and_missing_audio_reported(self):
        root = ET.Element('DJ_PLAYLISTS')
        collection = ET.SubElement(root, 'COLLECTION')
        uri = self.a.as_uri().replace('file:///', 'file://localhost/')
        ET.SubElement(collection, 'TRACK', TrackID='8', Name=self.local['title'], Artist='DJ A', TotalTime='120', Location=uri)
        ET.SubElement(collection, 'TRACK', TrackID='9', Name='Missing', Location=(self.root / 'missing.mp3').as_uri())
        xml = self.root / 'library.xml'
        ET.ElementTree(root).write(xml, encoding='utf-8', xml_declaration=True)
        before = xml.read_bytes()
        library, warnings = p.read_library(xml)
        self.assertEqual(library[0]['path'], str(self.a))
        self.assertEqual(warnings[0]['track_id'], '9')
        self.assertEqual(xml.read_bytes(), before)

    def test_nonlocal_and_xml_entities_rejected(self):
        for location in ('https://host/file.mp3', 'file://server/share/music.mp3'):
            with self.assertRaises(ValueError):
                p.xml_local_path(location)
        xml = self.root / 'bad.xml'
        xml.write_text('<!DOCTYPE a [<!ENTITY x "oops">]><DJ_PLAYLISTS/>', encoding='utf-8')
        with self.assertRaises(ValueError):
            p.read_library(xml)

    def test_no_overwrite_and_audio_untouched(self):
        before = self.a.read_bytes()
        out = self.root / 'out'
        p.build(self.playlist(), [self.local], out)
        saved = (out / 'playlist.m3u8').read_bytes()
        with self.assertRaises(FileExistsError):
            p.build(self.playlist(), [self.local], out)
        self.assertEqual((out / 'playlist.m3u8').read_bytes(), saved)
        self.assertEqual(self.a.read_bytes(), before)

    def test_missing_empty_relative_or_control_path_rejected(self):
        empty = self.root / 'empty.wav'
        empty.touch()
        for value in (str(empty), str(self.root / 'missing.wav'), 'relative.wav', str(self.a) + '\nextra'):
            with self.assertRaises(ValueError):
                p.safe_path(value)

    def test_override_requires_evidence_and_known_position(self):
        with self.assertRaises(ValueError):
            p.build(self.playlist(), [], self.root / 'out', {'1': {'path': str(self.a), 'confirmed': True}})
        with self.assertRaises(ValueError):
            p.build(self.playlist(), [], self.root / 'out', {'2': {'path': str(self.a)}})
        report = p.build(self.playlist(), [], self.root / 'out', {'1': {'path': str(self.a), 'confirmed': True, 'evidence': 'User confirmed exact local file'}})
        self.assertEqual(report['matched_count'], 1)

    def test_partial_and_unavailable_preserved(self):
        tracks = [{**self.track, 'position': 2}, {'position': 4, 'status': 'unavailable'}]
        out = self.root / 'out'
        report = p.build(self.playlist(tracks, complete=False), [self.local], out)
        self.assertFalse(report['complete'])
        self.assertEqual(report['tracks'][1]['reason'], 'unavailable')
        with self.assertRaises(ValueError):
            p.validate_playlist(self.playlist(tracks))

    def test_no_artist_not_automatically_matched_to_uploader(self):
        track = p.sc_track({'title': self.local['title'], 'user': {'username': 'DJ A'}}, 1)
        self.assertEqual(track['artists'], [])
        self.assertIsNone(p.choose(track, [self.local])[0])

    def test_unmatched_csv_neutralizes_formulas(self):
        out = self.root / 'out'
        p.build(self.playlist([{**self.track, 'title': '=1+1'}]), [], out)
        with (out / 'unmatched.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][1], "'=1+1")

    def test_verify_detects_tampering_and_missing_files(self):
        out = self.root / 'out'
        p.build(self.playlist(), [self.local], out)
        self.a.unlink()
        with self.assertRaises(ValueError):
            p.verify(out)

    def spotify_getter(self, change=False, wrong_total=False):
        calls = []
        base = 'https://api.spotify.com/v1/playlists/EXAMPLE'
        def get(endpoint, host, token):
            calls.append(endpoint)
            if endpoint == base:
                return {'name': 'test', 'snapshot_id': 'changed' if change and len(calls) > 1 else 'one'}
            if 'offset=2' in endpoint:
                return {'offset': 2, 'total': 3, 'next': None, 'items': [{'track': {'name': 'same', 'artists': [{'name': 'A'}], 'type': 'track'}}]}
            return {'offset': 0, 'total': 4 if wrong_total else 3, 'next': base + '/items?offset=2',
                    'items': [{'item': {'name': 'same', 'artists': [{'name': 'A'}], 'type': 'track'}}, {'item': None}]}
        return get, calls

    def test_spotify_pages_keep_nulls_duplicates_and_old_new_schema(self):
        get, calls = self.spotify_getter()
        data = p.fetch_playlist(self.playlist()['source_url'], 'not-a-real-token', get)
        self.assertEqual(len(calls), 4)
        self.assertEqual([t['title'] for t in data['tracks']], ['same', '', 'same'])
        self.assertEqual(data['tracks'][1]['status'], 'unavailable')
        p.validate_playlist(data)

    def test_spotify_snapshot_or_count_change_stops(self):
        for kwargs in ({'change': True}, {'wrong_total': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                p.fetch_playlist(self.playlist()['source_url'], 'test', self.spotify_getter(**kwargs)[0])

    def test_foreign_pagination_or_stream_endpoint_refused(self):
        for raw, host in [('https://api.spotify.com.evil/v1/playlists/a/items', 'api.spotify.com'),
                          ('https://api.soundcloud.com/tracks/1/stream', 'api.soundcloud.com'),
                          ('http://api.spotify.com/v1/playlists/a/items', 'api.spotify.com'),
                          ('https://api.spotify.com@evil/v1/playlists/a/items', 'api.spotify.com')]:
            with self.assertRaises(ValueError):
                p.check_api(raw, host)

    def test_short_or_foreign_playlist_url_refused(self):
        for raw in ('https://evil.test/playlist/a', 'https://spotify.link/short', 'https://soundcloud.com/a/track'):
            with self.assertRaises(ValueError):
                p.source_url(raw)

    def test_soundcloud_stub_hydration_and_order(self):
        def get(endpoint, host, token):
            if '/resolve?' in endpoint:
                return {'kind': 'playlist', 'title': 'SC', 'track_count': 3, 'tracks': [{'id': 2}, {'id': 1}, {'id': 2}]}
            identifier = endpoint.rsplit('/', 1)[1]
            return {'id': identifier, 'title': 'track ' + identifier, 'metadata_artist': 'Artist', 'downloadable': True}
        data = p.fetch_playlist('https://soundcloud.com/a/sets/b', 'test', get)
        self.assertEqual([t['source_id'] for t in data['tracks']], ['2', '1', '2'])
        self.assertTrue(data['tracks'][0]['official_download_hint'])

    def test_soundcloud_truncated_list_stops(self):
        def get(*args):
            return {'kind': 'playlist', 'track_count': 10, 'tracks': [{'id': 1}]}
        with self.assertRaises(ValueError):
            p.fetch_playlist('https://soundcloud.com/a/sets/b', 'test', get)

    def test_cli_end_to_end(self):
        playlist, index = self.root / 'playlist.json', self.root / 'local.json'
        p.write_json(playlist, self.playlist())
        p.write_json(index, [self.local])
        out = self.root / 'cli'
        cmd = [sys.executable, str(Path(p.__file__)), 'build', '--playlist', str(playlist), '--local-index', str(index), '--out', str(out)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(p.verify(out), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
