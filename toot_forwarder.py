#!/bin/python3.6
import os
import sys
import re
import json
import requests
import feedparser
from mastodon import Mastodon
from datetime import datetime
import warnings
warnings.simplefilter("ignore", UnicodeWarning)

''' -----------------------------------------------------------------
Mastodon Toot Forwarder by itsumonotakumi

[Usage]
 python mastodon_toot_forwarder.py [FromHost] [FromUser]
                                   [ToHost] [ToUser] [TokenPath]
  - FromHost:  From instance's hostname
  - FromUser:  From instance's username
  - ToHost:    To instance's hostname
  - ToUser:    To instance's username
  - TokenPath: Json filepath contains ClientID/ClientSecret/AccessToken
'''
# 設定値 -------------------------------------------------------------
TOOT_ONLY = True  # FalseにするとBTやメンションも含める
TOOT_LIMIT = 0    # 同期対象にするトゥート数の制限(0:Atomで取得できる上限全て)
TMP_DIR = '/tmp'
START_DATE = '2017/07/31 00:00:00'
TO_VISIBILITY = 'public'  # direct/private/unlisted/public


# 実行コード ----------------------------------------------------------
# 引数の格納
if len(sys.argv) == 6:
    FROM_MSTDNHOST = str(sys.argv[1])
    FROM_USERNAME = str(sys.argv[2])
    TO_MSTDNHOST = str(sys.argv[3])
    TO_USERNAME = str(sys.argv[4])
    CLIENT_JSON = os.path.dirname(os.path.abspath(__file__)) + '/' + str(sys.argv[5])
else:
    raise Exception('Usage: python ' + os.path.basename(sys.argv[0]) + ' [From hostname] [From username] [To hostname] [To username] [TokenDataPath]')


# 対象とするトゥートの取得
def get_toot(Hostname, Username, Limit, Toots):

    # ATOMデータ取得
    Atom_Url = 'https://' + Hostname + '/@' + Username + '.atom'
    toot_dic = feedparser.parse(Atom_Url)

    # フィードが異常なら例外を発生
    if toot_dic.bozo == 1:
        raise Exception("Failed to get Atom data. URL: " + Atom_Url)

    # 取得したフィード1つ1つに対する処理
    toot_limit = 0
    for entry in toot_dic.entries:
        # 対象トゥート数の上限を越えたら終了
        if Limit != 0 and toot_limit >= Limit:
            break
        else:
            toot_limit += 1  # トゥート回数をインクリメント

        # 新規トゥートのみに絞り込む
        if TOOT_ONLY is True and not re.match('\ANew\sstatus', entry['title_detail']['value']):
            continue

        # リストの初期化
        toot_data = []
        img = []

        # ATOMからトゥート日時を取り出して、開始日以降かを確認する
        toot_date_str = str(entry['published_parsed'][0]) + '/' \
            + str(entry['published_parsed'][1]).rjust(2, '0') + '/' \
            + str(entry['published_parsed'][2]).rjust(2, '0') + ' ' \
            + str(entry['published_parsed'][3]).rjust(2, '0') + ':' \
            + str(entry['published_parsed'][4]).rjust(2, '0') + ':' \
            + str(entry['published_parsed'][5]).rjust(2, '0')
        start_time = datetime.strptime(START_DATE, '%Y/%m/%d %H:%M:%S')
        toot_date = datetime.strptime(toot_date_str, '%Y/%m/%d %H:%M:%S')
        if toot_date < start_time:
            continue

        # ATOMからのトゥート本文および添付ファイルのURLを取り出す
        toot_text = entry['content'][0]['value']
        for link_url in entry['links']:
            if link_url['rel'] == "enclosure":
                img.append({"href": link_url['href'], 'mime': link_url['type']})

        # 1つのトゥートをリスト化
        toot_data.append(toot_text)
        for outdata in img:
            toot_data.append(outdata)

        # 複数のトゥートまとめ
        Toots.append(toot_data)


# トゥートの重複排除
def check_toot(Hostname, Username, Check_From_Toots):

    # 転送先の直近トゥートを取得
    Check_To_Toots = []
    get_toot(Hostname, Username, 0, Check_To_Toots)

    # 転送先と転送元のトゥートを比較
    for tt in Check_To_Toots:
        for ft in Check_From_Toots:
            # Skip mention
            if TOOT_ONLY is True and re.match("@", ft[0]):
                Check_From_Toots.remove(ft)

            # まったく同じ投稿(テキスト)があれば重複排除
            p = re.compile(r"\s|\?|\t")
            if p.sub('', cleanup_toot(ft[0])) == p.sub('', cleanup_toot(tt[0])):
                Check_From_Toots.remove(ft)


# メディアファイルのダウンロードとURLからファイルパスへの置換
def get_media(Toots):

    for toot_data in Toots:
        for i in range(1, len(toot_data)):

            # ファイルのダウンロード
            res = requests.get(toot_data[i]['href'], allow_redirects=False, timeout=10)
            if res.status_code != 200:
                raise Exception("Downoading images failed: " + res.status_code)

            # メディアファイルの保存
            filename = ((toot_data[i]['href']).split("/"))[-1]
            filepath = os.path.join(TMP_DIR, filename)
            with open(filepath, "wb") as fout:
                fout.write(res.content)

            # ファイルパスの格納
            toot_data[i]['filepath'] = filepath


# トゥート本文の掃除
def cleanup_toot(text):

    p = re.compile(r"<\/p>")
    clean_html = p.sub("\n\n", text)

    p = re.compile(r"\u2028|<br>|<br \/>")
    clean_html = p.sub("\n", clean_html)

    p = re.compile(r"<[^>]*?>")
    clean_html = p.sub("", clean_html)

    return clean_html


# トゥート投稿(転送)
def post_toot(Hostname, Client_Json, To_Visibility, Toots):
    # 対象となるトゥートがなければ終了
    if len(Toots) == 0:
        sys.exit()

    # JSONデータの読み込み
    if not os.path.exists(Client_Json):
        raise Exception('Can\'t open file ' + Client_Json + ': [Errno] No such file ')
    with open(Client_Json, 'r', encoding='utf-8') as f:
        client_json = json.loads(f.read())

    # Mastodonへログイン
    Url = "https://" + Hostname
    mstdn = Mastodon(client_id=client_json['client_id'], client_secret=client_json['client_secret'], access_token=client_json['access_token'], api_base_url=Url)

    # 各トゥートを処理
    for toot_content in Toots:

        # メディアファイルがある場合の処理
        media_files = []
        if len(toot_content) > 1:
            for i in range(1, len(toot_content)):
                # media_files.append(mstdn.media_post(toot_content[i]['filepath'], toot_content[i]['mime'])) mimeタイプを指定する場合の処理
                media_files.append(mstdn.media_post(toot_content[i]['filepath']))

        # トゥート
        if len(media_files) > 0:
            # 画像あり
            mstdn.status_post(status=cleanup_toot(toot_content[0]), media_ids=media_files, visibility=To_Visibility)
        else:
            # 画像なしの場合
            mstdn.status_post(status=cleanup_toot(toot_content[0]), visibility=TO_VISIBILITY)


# メインルーチン
if __name__ == "__main__":

    # トゥートコンテンツ初期化
    Toot_Contents = []

    # 対象とするトゥートの取得
    get_toot(FROM_MSTDNHOST, FROM_USERNAME, TOOT_LIMIT, Toot_Contents)
    if len(Toot_Contents) == 0 or Toot_Contents is None:
        sys.exit()

    # トゥートの重複排除
    check_toot(TO_MSTDNHOST, TO_USERNAME, Toot_Contents)
    if len(Toot_Contents) == 0 or Toot_Contents is None:
        sys.exit()

    # メディアファイルのダウンロードとURLからファイルパスへの置換
    get_media(Toot_Contents)

    # トゥート投稿(転送)
    post_toot(TO_MSTDNHOST, CLIENT_JSON, TO_VISIBILITY, Toot_Contents)

    # 結果出力
    print(len(Toot_Contents), 'toots was forwarded from @' + FROM_USERNAME + '@' + FROM_MSTDNHOST + ' to @' + TO_USERNAME + '@' + TO_MSTDNHOST + '.')
