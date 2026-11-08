#!/usr/bin/env python3
"""DeepAIが探した候補を、実際のページで確かめてから服リストに取り込む。

使い方（ターミナル）:
  python3 ~/Desktop/Claudeの作業領域/服リスト/tools/取り込み.py            # 最新の結果を取り込んで公開
  python3 ~/Desktop/Claudeの作業領域/服リスト/tools/取り込み.py --dry      # 確認だけ（保存・公開しない）

確かめること: ページが存在するか / 本当の商品名・価格（メルカリは公開情報から読む）/ サムネイル画像
売り切れかどうかはページを開かないと分からないため「未確認」として残す。
"""
import glob, html, json, os, re, subprocess, sys, urllib.request, urllib.error

HOME = os.path.expanduser('~/Desktop/Claudeの作業領域')
RES = os.path.join(HOME, 'DeepAI作業/服さがし/結果')
REQ = os.path.join(HOME, 'DeepAI作業/服さがし/依頼.md')
APP = os.path.join(HOME, '服リスト')
SEED = os.path.join(APP, 'seed/seed.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'


def meta(page, key):
    m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(key), page)
    return html.unescape(m.group(1)) if m else ''


CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def render(url):
    """MacのChromeで画面を表示した後のHTMLを取る（売り切れ表示はこれでないと分からない）"""
    if not os.path.exists(CHROME):
        return ''
    try:
        r = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--virtual-time-budget=8000',
                            '--dump-dom', url], capture_output=True, timeout=60)
        return r.stdout.decode('utf-8', 'ignore')
    except Exception:
        return ''


def sold_state(url, dom):
    """on_sale / sold / unknown"""
    if not dom:
        return 'unknown'
    if 'mercari.com/item/' in url:
        m = re.search(r'data-testid="checkout-button"[^>]*name="([a-z]+)"', dom) or \
            re.search(r'name="([a-z]+)"[^>]*data-testid="checkout-button"', dom)
        if m:
            return 'on_sale' if m.group(1) == 'purchase' else 'sold'
        return 'unknown'
    if re.search(r'SOLD OUT|売り切れました|在庫なし|在庫切れ|販売終了|この商品は売り切れ', dom):
        return 'sold'
    return 'unknown'


def check(url):
    st, title, price, img = check_meta(url)
    if st == 'gone':
        return st, title, price, img, 'gone'
    dom = render(url)
    if st == 'unknown' and dom:  # curlでは弾かれたサイト（ZOZOなど）
        page_title = re.search(r'<title>([^<]*)', dom)
        if page_title and not re.search(r'404|見つかりません|ページが存在しません|Not Found', dom[:20000]):
            st, title = 'ok', html.unescape(page_title.group(1)).split('|')[0].strip()
        elif page_title:
            return 'gone', '', None, '', 'gone'
    return st, title, price, img, sold_state(url, dom)


def check_meta(url):
    """戻り値: (状態, 本当の商品名, 本当の価格, 画像URL)  状態は ok / gone / unknown"""
    # Pythonの証明書設定に左右されないよう、Mac標準のcurlで取得する
    r = subprocess.run(['curl', '-sL', '--max-time', '20', '-A', UA, '-H', 'Accept-Language: ja',
                        '-w', '\n%{http_code}', url], capture_output=True)
    out = r.stdout.decode('utf-8', 'ignore')
    page, _, code = out.rpartition('\n')
    if code in ('404', '410'):
        return 'gone', '', None, ''
    if code != '200':
        return 'unknown', '', None, ''
    title = re.sub(r'\s*(by|-)\s*(メルカリ|Yahoo!フリマ.*|ラクマ)$', '', meta(page, 'og:title'))
    price = meta(page, 'product:price:amount')
    img = meta(page, 'og:image')
    # 商品ページではなくトップや検索に飛ばされた＝消えた商品
    if 'mercari.com/item/' in url and '/item/' not in (meta(page, 'og:url') or url):
        return 'gone', '', None, ''
    if not title or title.startswith('Yahoo!フリマ'):
        return 'gone', '', None, ''
    return 'ok', title, int(float(price)) if price else None, img


def read_requests():
    out = {}
    for line in open(REQ, encoding='utf-8'):
        p = [x.strip() for x in line.split('|')]
        if len(p) >= 5 and re.match(r'^[su]\d+$', p[0]):
            out[p[0]] = {'name': p[1], 'kw': p[2], 'limit': int(p[3]) if p[3].isdigit() else None, 'kind': p[4]}
    return out


def main():
    dry = '--dry' in sys.argv
    files = sorted(glob.glob(os.path.join(RES, '*.json')), key=os.path.getmtime)
    if not files:
        sys.exit('結果ファイルがありません: ' + RES)
    src = files[-1]
    print('読み込み:', os.path.basename(src))
    data = json.load(open(src, encoding='utf-8'))
    reqs = read_requests()
    seed = json.load(open(SEED, encoding='utf-8'))
    byid = {s['id']: s for s in seed}
    total = kept = 0
    for block in data:
        iid = block.get('id')
        limit = reqs.get(iid, {}).get('limit')
        cands = []
        for c in block.get('items', []):
            total += 1
            url = c.get('url', '')
            st, title, price, img, sale = check(url)
            if st == 'gone':
                print(f'  ✗ {iid} 存在しない/削除済み: {url}')
                continue
            if st != 'ok':  # 開けなかったページは信用しない
                print(f'  ✗ {iid} ページを確認できず: {url}')
                continue
            if sale == 'sold':
                print(f'  ✗ {iid} 売り切れ: {title or c.get("title")}')
                continue
            p = price if price is not None else c.get('price')
            if price is not None and c.get('price') and price != c.get('price'):
                print(f'  △ {iid} 価格が違った {c.get("price")}→{price}: {title}')
            if limit and p and p >= limit:
                print(f'  ✗ {iid} 上限価格以上 ¥{p}: {title or c.get("title")}')
                continue
            cands.append({'t': title or c.get('title', ''), 'p': p or 0, 'u': url, 'img': img,
                          'size': c.get('size', ''), 'why': c.get('why', ''),
                          'chk': '販売中を確認' if sale == 'on_sale' else '売り切れ未確認' if st == 'ok' else '未確認'})
            kept += 1
            print(f'  ✓ {iid} ¥{p} {cands[-1]["t"]} [{cands[-1]["chk"]}]')
        cands.sort(key=lambda x: x['p'] or 10**9)
        if iid in byid:
            byid[iid]['cands'] = cands
        elif iid in reqs:  # 依頼.md に足した新しいアイテム
            r = reqs[iid]
            item = {'id': iid, 'name': r['name'], 'kw': r['kw'], 'cat': {'靴': 'シューズ', 'バッグ': 'バッグ'}.get(r['kind'], 'トップス'),
                    'price': r['limit'] or '', 'shop': '', 'coord': '', 'status': 'want', 'photos': [], 'memo': '', 'cands': cands}
            seed.append(item); byid[iid] = item
    print(f'\n{total}件中 {kept}件を採用')
    if dry:
        print('（--dry なので保存していません）'); return
    json.dump(seed, open(SEED, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    subprocess.run(['git', '-C', APP, 'add', 'seed/seed.json'], check=True)
    if subprocess.run(['git', '-C', APP, 'diff', '--cached', '--quiet']).returncode == 0:
        print('変更なし'); return
    subprocess.run(['git', '-C', APP, 'commit', '-qm', '候補を更新: ' + os.path.basename(src)], check=True)
    subprocess.run(['git', '-C', APP, 'push', '-q'], check=True)
    print('公開しました。1〜2分後にアプリを開き直すと反映されます。')


if __name__ == '__main__':
    main()
