# 実行と入力形式

Python 3.10以上。基本処理は標準ライブラリだけで動作する。以下はSkillフォルダを作業場所とした例。実行時は実在する絶対パスに置き換え、シェルの引数として安全に引用する。出力先は毎回新規フォルダを指定する。

## 1. メタデータ取得

```text
python scripts/playlist_rebuild.py fetch --url "https://open.spotify.com/playlist/PLAYLIST_ID" --output "playlist.json"
python scripts/playlist_rebuild.py fetch --url "https://soundcloud.com/ARTIST/sets/PLAYLIST" --output "playlist.json"
```

Spotifyはユーザーの正規OAuthアクセストークンを`SPOTIFY_ACCESS_TOKEN`、SoundCloudは`SOUNDCLOUD_ACCESS_TOKEN`で渡す。パスワード、ブラウザcookie、他アプリのclient_idを取得しない。トークン発行と更新の仕組みはこのスクリプトに含まない。ユーザーが安全な方法で設定し、Codexは値を表示しない。Spotifyの現行APIには所有者／共同編集者の制約があり、任意の公開URLが必ず取得できるわけではない。

APIはHTTPエラー時に自動再試行しない。429なら示された待ち時間と再実行可否を確認し、401/403なら権限を確認する。途中失敗では完全な曲一覧として保存しない。Spotifyは全ページを取得し、offset、total、前後のsnapshotを確認する。SoundCloudは公式resolveが返す順序付きtracks配列とtrack_countを照合し、IDだけの項目の情報を公式APIから補完する。省略されたリストなら停止してブラウザ／入力ファイルへ切り替える。短縮URLは公式サイトで展開してから使用する。

APIが使えなければ、利用可能なブラウザ操作ツールで公式ページの曲一覧を読む。遅延読込やスクロールで全件表示されているか確認し、検索エンジンの抜粋から完全なプレイリストを推定しない。認証済みのユーザーが提供した曲一覧も利用可能。以下のJSONを作成する。`complete=true`は元の件数と順序を確認できた場合のみ。部分取得は`complete=false`、不明な総数は`expected_count=null`。削除曲の位置が判明していれば`status=unavailable`で空欄のまま残す。重複曲はそれぞれ別のpositionで残す。

```json
{
  "schema_version": 1,
  "name": "夜のセット",
  "source_url": "https://open.spotify.com/playlist/EXAMPLE",
  "complete": true,
  "expected_count": 2,
  "tracks": [
    {"position": 1, "title": "Track A (Extended Mix)", "artists": ["Artist A"], "duration_ms": 360000, "isrc": "", "source_url": "", "status": "ready"},
    {"position": 2, "title": "", "artists": [], "status": "unavailable"}
  ]
}
```

SoundCloudの投稿者名はアーティスト名とは限らないため、スクリプトは`metadata_artist`等がない場合、artistsを空欄にする。曲名の「Artist - Title」は自動分割しない。公式情報またはユーザー確認で補う。

## 2. 既存ライブラリを優先照合

rekordboxの［ファイル］→［コレクションをxml形式でエクスポート］等からコレクションを書き出す。バージョンによってメニュー名が異なるため画面を確認する。XMLを渡せない場合は指定音源のタグまたは下記JSONを使う。

```text
python scripts/playlist_rebuild.py build --playlist "playlist.json" --rekordbox-xml "C:/DJ/rekordbox.xml" --local-index "local.json" --out "C:/DJ/results/run-001"
python scripts/playlist_rebuild.py verify --out "C:/DJ/results/run-001"
```

`--rekordbox-xml`、`--local-index`、`--audio-dir`は複数指定可能。音源フォルダのタグを読む場合はmutagenが必要（環境にない場合は通常の依存追加手順を使う）。音源名だけでは自動一致させない。タグがないWAV/AIFF等はXMLまたは`local.json`を利用する。

`local.json`は以下の配列。パスは実在し、ゼロバイトでない、利用権を確認した音源を指定する。相対パス、Web URL、未完了のダウンロードは不可。破損やDRMの検出は拡張子チェックだけでは不十分なので、取得段階で再生可能性を確認する。

```json
[
  {"path": "C:/DJ/Music/Artist A - Track A.flac", "title": "Track A (Extended Mix)", "artists": ["Artist A"], "duration_ms": 360000, "isrc": ""}
]
```

照合はUnicode NFKC・空白・大文字小文字の正規化後の曲名＋アーティスト一致、または同じ曲名＋ISRC一致。長さが両方ある場合は3秒以内。バージョン表記や句読点を消さない。同じ品質の候補なら既存XMLを優先し、その中で複数あれば未確定とする。長さ不明の一致は`duration_checked=false`で報告。同じファイルは候補として重複させないが、プレイリスト内の重複登場は残す。

XMLの`file://localhost/` URIは一度だけデコードする。日本語・空白・#・%に対応。別OSのパスやネットワークホストのURIは勝手に変換せず、接続先・移設先をユーザーに確認してローカル音源JSONへ登録する。XMLで欠落した音源は`report.json`のwarningsに残る。

## 3. 候補確定／公式取得後の対応付け

ユーザーが候補を指定した、または公式ダウンロードで元曲と保存ファイルの対応が確認できた場合のみ、元のpositionをキーにしたマップを作る。`confirmed=true`はプログラム上の入力条件であり権利の検証機能ではない。Codexが根拠なしに追加しない。

```json
{
  "1": {
    "path": "C:/DJ/Music/Artist A - Track A.flac",
    "confirmed": true,
    "evidence": "元曲のSoundCloud公式Download fileから保存し、曲名・バージョン・再生を確認",
    "source_url": "https://soundcloud.com/artist/track",
    "method": "soundcloud_official_download",
    "retrieved_at": "2026-11-20T12:00:00+09:00"
  }
}
```

```text
python scripts/playlist_rebuild.py build --playlist "playlist.json" --rekordbox-xml "C:/DJ/rekordbox.xml" --overrides "overrides.json" --out "C:/DJ/results/run-002"
```

## 出力と検証の範囲

- `playlist.m3u8`: 一致した音源の絶対パス。順序・重複を維持。
- `report.json`: 全位置、照合理由、候補、警告、取得の完了性、公式取得の根拠。
- `unmatched.csv`: UTF-8 BOM付き。元位置・曲名・理由・候補。表計算ソフトで数式扱いされる先頭文字をエスケープ。
- `IMPORT.md`: 日本語の取り込み手順と件数。

`verify`は生成物の整合とファイル実在を検査する。実際の音声内容、利用権、rekordbox GUIでの重複保持・取り込み動作は検証しない。元リストの全件性が不明なら、件数が合っていても完全再現と報告しない。未一致0件の空プレイリストとAPI取得失敗を混同しない。
