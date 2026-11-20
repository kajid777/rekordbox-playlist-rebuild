# 参照した公式仕様

確認日: 2026-11-20。APIやUI変更が疑われる場合に再確認する。

- Spotify [Get Playlist Items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items): `/v1/playlists/{id}/items`、ページング、現在の所有者／共同編集者制約。403は回避せず曲一覧入力へ切り替える。
- Spotify [2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide): `items/item`への変更。補助スクリプトは旧レスポンスの`tracks/track`も読める。
- SoundCloud [API Guide](https://developers.soundcloud.com/docs/api/guide): OAuth、resolve、playlists、ページング、metadata_artist。再生ストリームは原音ダウンロードではない。
- SoundCloud [Downloading tracks](https://help.soundcloud.com/hc/en-us/articles/115003448787-Downloading-tracks): 公式Download fileと一曲単位のダウンロード。
- rekordbox [日本語マニュアル 7.2.14](https://cdn.rekordbox.com/files/20260409170202/rekordbox7.2.14_manual_JA.pdf): M3U8をファイル→インポート→プレイリストをインポートから取り込む。
- rekordbox [XML仕様](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf): COLLECTION/TRACKのLocation、Artist、Name、TotalTime等。

このSkillはXMLを入力に用い、M3U8を出力する。新規XMLのTrackIDやキューを生成して既存ライブラリに上書きする処理は持たない。
