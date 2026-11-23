"""Archive the supplied product page and its product images without site scripts."""
from pathlib import Path
from html import unescape
import re
import json
import hashlib
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'modules' / 'PD诱骗模块'
URL = 'https://www.xinlucity.com/?s=resourcedetail/index/id/608.html'
request = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(request, timeout=45) as response:
    raw = response.read().decode(response.headers.get_content_charset() or 'utf-8')
start = raw.index('<div class="content-web ')
end = raw.index('<!-- 资料下载 -->', start)
fragment = raw[start:end]
# The product-richtext wrapper ends before the next stylesheet.
fragment = fragment.split('<style>')[0].strip()
fragment = re.sub(r'\sstyle="[^"]*"', '', fragment)
fragment = re.sub(r'\s(?:class|width|title)="[^"]*"', '', fragment)
images = list(dict.fromkeys(re.findall(r'https://testxinlu\.oss-cn-beijing\.aliyuncs\.com/static/upload/images/warehouse/[^"\s<>]+', raw)))
names = ['产品主图.png', 'A款焊盘配置图.png', 'B款焊盘配置图.png']
assert len(images) == len(names), 'Product page image layout changed; inspect before archiving'
DEST.mkdir(exist_ok=True, parents=True)
(DEST / 'images').mkdir(exist_ok=True)
records = []
for url, name in zip(images, names):
    target = DEST / 'images' / name
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    target.write_bytes(urllib.request.urlopen(request, timeout=45).read())
    fragment = fragment.replace(url, 'images/' + name)
    records.append({'path': str(target.relative_to(DEST)).replace('\\', '/'), 'source_url': url})
(DEST / '网页原始快照.html').write_text(raw, encoding='utf-8')
stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
offline = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PD诱骗模块网页资料离线版</title><style>
body{font:16px/1.7 'Microsoft YaHei',sans-serif;max-width:960px;margin:36px auto;padding:0 24px;color:#202b34}
table{border-collapse:collapse;width:100%;margin:18px 0}td,th{border:1px solid #ccd4dc;padding:8px}
th{background:#eef3f7}img{max-width:100%;height:auto}blockquote{border-left:4px solid #657e98;margin:16px 0;padding:8px 18px;background:#f1f5f8}
li{margin:5px 0}h1{font-size:28px}a{color:#235f94}
</style><h1>Mini PD3.1 快充诱骗模块 A 款和 B 款</h1>
<p>来源 <a href="''' + URL + '''">芯路城产品资料页 608</a>。采集时间 ''' + stamp + '''。</p>
<p>以下保存网页详情文字与产品图片，移除了站点导航、登录组件、跟踪和脚本。网站的资料下载栏目在采集时显示暂无内容。</p>'''
(DEST / '网页资料离线版.html').write_text(offline + fragment + '</html>', encoding='utf-8')
records += [{'path': '网页原始快照.html', 'source_url': URL}, {'path': '网页资料离线版.html', 'source_url': URL, 'derived': True}]
for item in records:
    data = (DEST / item['path']).read_bytes()
    item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
(DEST / '来源清单.json').write_text(json.dumps({'retrieved_utc': stamp, 'source_url': URL, 'attachments': [], 'attachment_status': '资料下载栏目显示暂无内容', 'files': records}, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'saved': len(records), 'images': len(images), 'directory': str(DEST)}, ensure_ascii=False))
