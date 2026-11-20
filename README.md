# rekordbox-playlist-rebuild

Spotify / SoundCloudのプレイリストを、利用権のある手持ち音源でrekordbox向けに再現するCodex Skillです。

- 元の曲順と重複を保持し、既存rekordbox XMLのローカル音源を優先照合
- 同名の別リミックス、複数候補、欠落ファイルは未一致レポートに記録
- UTF-8 M3U8、日本語のインポート手順、JSONレポート、未一致CSVを生成
- 公式に許可された音源取得の手順を収録。Pythonスクリプトは音声をダウンロードしません

## Codexで使う

このリポジトリの内容を、Codexが読み込むSkillフォルダの `rekordbox-playlist-rebuild` ディレクトリに配置します。`SKILL.md`がそのディレクトリの直下にある状態にしてください。

```text
$rekordbox-playlist-rebuild
このプレイリストURLを、既存rekordboxライブラリ優先で再現してください。
URL: （SpotifyまたはSoundCloudのプレイリストURL）
rekordbox XML: （書き出したXMLの保存先）
利用権のある音源フォルダ: （保存先）
```

詳細は [SKILL.md](SKILL.md) と [実行手順](references/workflow.md) を参照してください。

## スクリプト

Python 3.10以上。基本処理は標準ライブラリのみで動作します。音源フォルダのタグ読み取りには任意依存の `mutagen` が必要です。

```sh
python scripts/playlist_rebuild.py --help
python scripts/test_playlist_rebuild.py
```

22項目のオフラインテストをWindows / Python 3.12で確認済みです。実アカウントへのAPI接続、公式ダウンロードの実操作、rekordbox GUIでの取り込みは未検証です。

SpotifyのAPI権限制約などにより、URLから全件取得できない場合があります。公式ページで確認した曲一覧JSONからも続行できます。トークンやローカル音源、個人ライブラリをリポジトリに追加しないでください。

SpotifyのDRM回避、ストリーム録音、無断ダウンロード、第三者ダウンロードサイトの利用は含みません。
