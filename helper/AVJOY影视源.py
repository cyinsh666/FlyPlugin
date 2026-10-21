#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AVJOY (avjoy.me) TVBox / CatVod / FongMi / drpyS-T4 爬虫源.

分类体系(取自站点两个目录页, 运行时动态抓取):
  /categories  → 17 个真实分类(slug + 中日英混排名称 + 影片数 + 图标)
  /tags        → 300 个热门标签(名称 + 影片数, 按站点热度排序, 分 15 个 Rank 组)

站点契约(实测确认):
  列表   GET /videos?page=N&o={mr|mv|tr|tf|old}        16 条/页
  分类   GET /videos/{slug}?page=N[&o=...]
  标签   GET /search/videos/{tag}/1?page=N[&o=...]     标签无独立路由, 复用搜索
  搜索   GET /search/videos/{kw}/1?page=N              路径段固定 /1, 真分页靠 ?page=
  详情   GET /video/{id}                               <source src label res>
  播放   media-cdn3.avjoy.me/{video|av1}/<token>/<ts>/<id>_<res>.mp4
         · token 每次请求详情页都会变 → 只能点击播放时实时解析, 禁止缓存
         · 必须带 Referer: https://avjoy.me/ , 否则 CDN 只回 23KB 占位文件
  海报   media-cdn2.avjoy.me/...  只校验 UA 非空, 空 UA 403, 与 Referer 无关
  站点偶发 TLS EOF → 内置退避重试
"""

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        pass

SITE = 'https://avjoy.me'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36')

# /videos?o=xx 与 /videos/{slug}?o=xx 通用, 搜索端点同样接受
SORTS = [('mr', '最新'), ('mv', '最受歡迎'), ('tr', '最高評分'),
         ('tf', '最愛'), ('old', '最早')]

TAG_CLASS = 'tags'          # 兼容旧 type_id
TAG_GROUP_PREFIX = 'tg_'    # 标签父组(一级) type_id 前缀
TAG_ITEM_PREFIX = 'tk_'     # 标签子项(二级) type_id 前缀
TAG_ROW_SIZE = 40           # filter 模式下每行标签数

# 标签父子分组规则: (组名, 判定函数标识)。顺序即呈现顺序。
TAG_GROUPS = [
    ('hot', '🔥 熱門前20'),
    ('region', '🌏 地區・語言'),
    ('genre', '🎬 類型・玩法'),
    ('studio', '🏢 片商・平台'),
    ('jp', '🇯🇵 日系女優'),
    ('cn', '🇨🇳 華語女優'),
    ('en', '🔤 英文・番號'),
    ('other', '📦 其他'),
]

# 人工归类词典(优先于字符集自动判定)。实测 /tags 前 300 个标签覆盖到位。
TAG_REGION = {
    '中文字幕', '無碼中文字幕', '國產av', '台灣', '台湾', '韓國', '韓語', '日本',
    '中國', '泰國', '香港', '歐美', '亞洲', 'chinese', 'japanese', 'korean',
    'thai', 'asian', 'japanese anime',
}
TAG_STUDIO = {
    'swag', 'heydouga', 'fc2', 'onlyfans', 'jvid', 'hentaipei', 'ed mosaic',
    'minamist', 'bvpp', 'amyluck', 'enkai', 'javcollectionhd', 'cici250808',
    'feifeibebe', '杏吧', '獨家', '麻豆', '天美', '蜜桃', '星空', '網紅',
}
TAG_GENRE = {
    '口交', '潮吹', '舔穴', '中出內射', '自慰', '自拍', '探花', '探花國產',
    '無碼', '無碼破解', '無碼破壞', '無碼流出', '巨乳', '眼鏡', '短髮', '啦啦隊',
    '電影', '動畫', '制服', '絲襪', '內射', '肛交', '足交', '乳交', '素人',
    'webcam', 'pov', 'milf', 'amateur', 'babe', 'blowjob', 'creampie',
    'cumshot', 'big-ass', 'big-tits', 'bigtit', 'anal', 'hentai', 'boot',
}

# /categories 抓取失败时的兜底(slug, 名称)
FALLBACK_CATEGORIES = [
    ('jav', 'JAV・日本AV'),
    ('china', 'China・中國'),
    ('asian', 'Asian・アジア'),
    ('japan', 'Japan・日本'),
    ('uncensored', 'Uncensored・無修正'),
    ('pornstar', 'Pornstar·AV女優'),
    ('big-tits', 'Big Tits・巨乳'),
    ('amateur', 'Amateur・素人'),
    ('korea', 'Korea・韓国・대한민국'),
    ('anime', 'Anime・アニメ'),
    ('schoolgirl', 'Schoolgirl・女子校生'),
    ('teen', 'Teen・ギャル・少女'),
    ('mature', 'Mature・熟女'),
    ('wife', 'Wife・人妻'),
    ('cosplay', 'Cosplay・コスプレ'),
    ('anal', 'Anal・アナル・肛交'),
    ('sm', 'SM'),
]

# /tags 抓取失败时的兜底(站点热度前列)
FALLBACK_TAGS = ['中文字幕', '口交', '潮吹', '舔穴', '中出內射', '國產av',
                 '台灣', '自拍', '無碼破解', '探花', 'swag', 'heydouga',
                 'しろハメ', '無碼流出', '巨乳', '眼鏡', '短髮', '啦啦隊']

_RE_KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
_RE_HAN = re.compile(r'[\u4e00-\u9fff]')
_RE_HANGUL = re.compile(r'[\uac00-\ud7af]')
_RE_ASCII = re.compile(r'^[\x20-\x7e]+$')


class Spider(_BaseSpider):
    # drpyS/T4Handler 实例化时会注入 t4_api; **kwargs 兼容各版本运行器
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = 'AVJOY影视'

    def __init__(self, t4_api="", **kwargs):
        """初始化本源请求状态与缓存，不启动媒体下载。"""
        self.t4_api = t4_api or kwargs.get('t4_api', '')
        self.site = SITE
        self.extend = []          # T4 缓存提交阶段直接访问, 必须是公开属性
        self._extend = {}
        self._proxy = ''
        self._ctx = ssl.create_default_context()
        self._cache = {}          # url -> (ts, html)
        self._ttl = 600
        self._cats = []           # [{slug,name,pic,count}]
        self._tags = []           # [{name,count}]
        self._seen = {}           # scope -> {page: [raw ids]}  跨页去重用

    # ---------- 生命周期 ----------
    def getName(self):
        """返回本独立插件的固定名称。"""
        return 'AVJOY影视'

    def getDependence(self):
        """声明本源使用的已知依赖，不执行依赖安装。"""
        return []

    def manualVideoCheck(self):
        """保留手动检查入口，不触发媒体下载。"""
        return False

    def isVideoFormat(self, url):
        """保留格式检查入口，最终格式由真实媒体结果判断。"""
        u = str(url or '').lower()
        return any(x in u for x in ('.mp4', '.m3u8', '.flv', '.ts', '.mkv'))

    def destroy(self):
        """释放本源的会话或缓存。"""
        self._cache.clear()

    def action(self, action):
        """保留源动作接口，不执行额外用户操作。"""
        return {}

    def localProxy(self, param):
        """处理本源显式发布的代理资源。"""
        return [404, 'text/plain', b'', {}]

    def init(self, extend=""):
        """初始化独立内容源配置，保留原站正常访问规则。"""
        self.extend = extend if extend is not None else []
        cfg = {}
        if isinstance(extend, dict):
            cfg = extend
        elif isinstance(extend, str) and extend.strip():
            s = extend.strip()
            if s.startswith('{'):
                try:
                    cfg = json.loads(s) or {}
                except Exception:
                    cfg = {}
            elif s.startswith('http'):
                cfg = {'host': s}
        elif isinstance(extend, list) and extend:
            if isinstance(extend[0], dict):
                cfg = extend[0]
        self._extend = cfg
        host = str(cfg.get('host') or '').strip().rstrip('/')
        if host.startswith('http'):
            self.site = host
        self._proxy = str(cfg.get('proxy') or '').strip()
        self._cache.clear()
        self._cats = []
        self._tags = []
        self._seen = {}
        return {}

    # ---------- 网络 ----------
    def _headers(self, referer=None):
        """生成访问原站页面所需的公开请求头。"""
        return {
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,ja;q=0.8,en;q=0.7',
            'Accept-Encoding': 'identity',
            'Referer': referer or (self.site + '/'),
            'Connection': 'close',
        }

    def _opener(self):
        """按源配置创建网络连接处理器。"""
        handlers = [urllib.request.HTTPSHandler(context=self._ctx)]
        if self._proxy:
            handlers.append(urllib.request.ProxyHandler(
                {'http': self._proxy, 'https': self._proxy}))
        return urllib.request.build_opener(*handlers)

    def _get(self, url, tries=2, cache=True):
        """读取源站真实 HTML；拒绝访问或网络失败不伪装成空目录。"""
        now = time.time()
        if cache:
            cached = self._cache.get(url)
            if cached and now - cached[0] < self._ttl:
                return cached[1]
        for attempt in range(max(1, int(tries))):
            try:
                request = urllib.request.Request(url, headers=self._headers())
                with self._opener().open(request, timeout=15) as response:
                    html = response.read().decode("utf-8", "replace")
                if not html.strip():
                    return self.sourceFailure("源站页面为空")
                if cache and len(html) > 2000:
                    self._cache[url] = (now, html)
                return html
            except Exception:
                if attempt + 1 >= max(1, int(tries)):
                    return self.sourceFailure("源站访问失败或拒绝请求")
                time.sleep(0.5)

    # ---------- 解析工具 ----------
    @staticmethod
    def _main_area(html):
        """截主内容区: 顶部导航的『精選視頻』侧栏也含 /video/ 链接, 必须排除。"""
        i = html.find('well-filters')
        body = html[i:] if i > 0 else html
        j = body.find('content-right')
        return body[:j] if j > 0 else body

    @staticmethod
    def _clean(s):
        """清理页面标签和空白，保留真实文字。"""
        s = re.sub(r'<[^>]+>', ' ', s or '')
        s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
        s = s.replace('&quot;', '"').replace('&#039;', "'")
        s = s.replace('&lt;', '<').replace('&gt;', '>')
        return ' '.join(s.split())

    def _cards(self, html):
        """列表卡片。按 <a href="/video/id/slug"> 锚点 + 局部窗口取字段,
        不依赖易变的外层 col-* 容器(该站不同页面容器类名不一致)。"""
        body = self._main_area(html)
        out, seen = [], set()
        for m in re.finditer(r'<a href="/video/(\d+)/([^"]*)"', body):
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            win = body[m.start():m.start() + 2600]
            img = re.search(r'<img[^>]+src="([^"]+)"', win)
            title = re.search(r'class="content-title">([^<]+)<', win)
            if not title:
                title = re.search(r'<img[^>]+title="([^"]*)"', win)
            if not title or not self._clean(title.group(1)):
                continue
            dur = re.search(r'class="duration">([\s\S]*?)</div>', win)
            views = re.search(r'class="content-views">\s*([^<]*?)\s*</span>', win)
            remark = self._clean(dur.group(1)) if dur else ''
            vs = self._clean(views.group(1)) if views else ''
            if vs:
                remark = (remark + ' · ' + vs).strip(' ·')
            out.append({
                'vod_id': vid,
                'vod_name': self._clean(title.group(1)),
                'vod_pic': img.group(1) if img else '',
                'vod_remarks': remark,
            })
        return out

    @staticmethod
    def _total(html):
        """读取原站显示的真实总量。"""
        m = re.search(r'中\s*<span class="text-highlighted">([\d,]+)</span>', html)
        if m:
            try:
                return int(m.group(1).replace(',', ''))
            except Exception:
                return 0
        return 0

    def _pagecount(self, html, per=16):
        """读取原站的总页数或分页链接。"""
        total = self._total(html)
        if total > 0:
            return max(1, min((total + per - 1) // per, 9999))
        pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', html)]
        return max(pages) if pages else 0

    # ---------- 分类 / 标签动态抓取 ----------
    def _fetch_categories(self):
        """/categories → [{slug,name,pic,count}]，按影片数降序。失败回退常量表。"""
        if self._cats:
            return self._cats
        body = self._main_area(self._get(self.site + '/categories'))
        out = []
        for m in re.finditer(
                r'href="/videos/([a-z0-9\-]+)">\s*<div class="thumb-overlay">\s*'
                r'<img src="([^"]*)"[^>]*?title="([^"]*)"'
                r'[\s\S]{0,500}?title-truncate">\s*([\s\S]*?)\s*</div>'
                r'[\s\S]{0,260}?float-right">\s*([\d,]*)\s*</div>', body):
            slug = m.group(1)
            pic = m.group(2) or ''
            if pic.startswith('/'):
                pic = self.site + pic
            name = self._clean(m.group(4)) or self._clean(m.group(3)) or slug
            try:
                cnt = int((m.group(5) or '0').replace(',', ''))
            except Exception:
                cnt = 0
            out.append({'slug': slug, 'name': name, 'pic': pic, 'count': cnt})
        if not out:
            out = [{'slug': s, 'name': n, 'pic': '', 'count': 0}
                   for s, n in FALLBACK_CATEGORIES]
        out.sort(key=lambda x: (-x['count'], x['name']))
        self._cats = out
        return out

    def _fetch_tags(self):
        """/tags → [{name,count}]，保留站点热度顺序。失败回退常量表。"""
        if self._tags:
            return self._tags
        body = self._main_area(self._get(self.site + '/tags'))
        out, seen = [], set()
        for m in re.finditer(
                r'class="tag-item">[\s\S]{0,320}?class="tag-counter">([\d,]+)</span>'
                r'[\s\S]{0,240}?href="/search/videos/([^"]+)"[^>]*title="([^"]*)"', body):
            name = self._clean(m.group(3)) or urllib.parse.unquote(m.group(2))
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                cnt = int(m.group(1).replace(',', ''))
            except Exception:
                cnt = 0
            out.append({'name': name, 'count': cnt})
        if not out:
            out = [{'name': n, 'count': 0} for n in FALLBACK_TAGS]
        self._tags = out
        return out

    @staticmethod
    def _fmt_count(n):
        """格式化原站返回的数量。"""
        if not n:
            return ''
        if n >= 10000:
            return '%.1f萬' % (n / 10000.0)
        return str(n)

    @staticmethod
    def _tag_bucket(name):
        """把标签归入父分组。人工词典优先, 再按字符集判定。"""
        low = name.lower()
        if low in TAG_STUDIO or name in TAG_STUDIO:
            return 'studio'
        if low in TAG_REGION or name in TAG_REGION:
            return 'region'
        if low in TAG_GENRE or name in TAG_GENRE:
            return 'genre'
        if _RE_HANGUL.search(name):
            return 'region'
        if _RE_KANA.search(name):
            return 'jp'
        if _RE_ASCII.match(name):
            return 'en'
        if _RE_HAN.search(name):
            return 'cn' if 2 <= len(name) <= 4 else 'genre'
        return 'other'

    def _tag_tree(self):
        """标签父子结构: {group_key: [ {name,count}, ... ] }，保留站点热度序。
        热门前 20 单独成组, 其余按语义分组。"""
        tags = self._fetch_tags()
        tree = {}
        for k, _ in TAG_GROUPS:
            tree[k] = []
        for i, t in enumerate(tags):
            if i < 20:
                tree['hot'].append(t)
            tree[self._tag_bucket(t['name'])].append(t)
        return tree

    def _tag_rows(self, items):
        """一个父分组内的标签切成多行筛选条, 每行 TAG_ROW_SIZE 个。"""
        rows = []
        for i in range(0, len(items), TAG_ROW_SIZE):
            chunk = items[i:i + TAG_ROW_SIZE]
            rows.append({
                'key': 'tag%d' % (i // TAG_ROW_SIZE),
                'name': '標籤' if i == 0 else ('標籤 %d+' % (i + 1)),
                'value': [{'n': self._tag_label(t), 'v': t['name']} for t in chunk],
            })
        return rows

    def _tag_label(self, t):
        """使用原站标签与数量生成分类显示名。"""
        c = self._fmt_count(t['count'])
        return ('%s (%s)' % (t['name'], c)) if c else t['name']

    @staticmethod
    def _parse_ext(extend):
        """壳下发 extend 的形态不统一: dict / JSON 字符串 / 空。
        之前只认 dict, 字符串形态被静默忽略 → 筛选选了也没反应。"""
        if isinstance(extend, dict):
            return {str(k): ('' if v is None else str(v)) for k, v in extend.items()}
        if isinstance(extend, str) and extend.strip():
            s = extend.strip()
            if s.startswith('{'):
                try:
                    d = json.loads(s)
                    if isinstance(d, dict):
                        return {str(k): ('' if v is None else str(v)) for k, v in d.items()}
                except Exception:
                    pass
            elif '=' in s:                      # a=b&c=d 形态
                try:
                    q = urllib.parse.parse_qs(s)
                    return {k: (v[0] if v else '') for k, v in q.items()}
                except Exception:
                    pass
        return {}

    @staticmethod
    def _tag_tid(name):
        """将真实标签编码为稳定分类标识。"""
        return TAG_ITEM_PREFIX + urllib.parse.quote(name, safe='')

    @staticmethod
    def _tid_tag(tid):
        """从稳定分类标识恢复真实标签。"""
        return urllib.parse.unquote(tid[len(TAG_ITEM_PREFIX):])

    # ---------- 五个契约接口 ----------
    def homeContent(self, filter=None):
        """发布原站分类与真实标签子类，标准协议无需依赖动态下钻 class。"""
        cats = self._fetch_categories()
        tree = self._tag_tree()

        # 关键变量：此目录真实请求无分类筛选的 /videos，供协议端识别全站目录语义。
        classes = [{'type_id': 'all', 'type_name': '全部影片', 'type_all': True}]
        for c in cats:
            cnt = self._fmt_count(c['count'])
            classes.append({
                'type_id': c['slug'],
                'type_name': ('%s (%s)' % (c['name'], cnt)) if cnt else c['name'],
            })

        sort_row = self._sort_row()
        filters = {'all': [sort_row]}
        for c in cats:
            filters[c['slug']] = [sort_row]

        # 关键变量：同一标签只发布一次，避免多个父组使用相同的作品分类 ID。
        published_tags = set()
        for key, label in TAG_GROUPS:
            items = [item for item in (tree.get(key) or []) if item['name'] not in published_tags]
            if not items:
                continue
            tid = TAG_GROUP_PREFIX + key
            classes.append({'type_id': tid,
                            'type_name': '%s (%d)' % (label, len(items))})
            filters[tid] = [sort_row] + self._tag_rows(items)
            # 每个子标签也带排序筛选
            for t in items:
                child_id = self._tag_tid(t['name'])
                published_tags.add(t['name'])
                classes.append({'type_id': child_id, 'type_name': self._tag_label(t), 'type_pid': tid})
                filters[child_id] = [sort_row]

        return {'class': classes, 'filters': filters}

    @staticmethod
    def _sort_row():
        """发布源站已知的排序选项。"""
        return {'key': 'o', 'name': '排序',
                'value': [{'n': n, 'v': v} for v, n in SORTS]}

    def homeVideoContent(self):
        """读取原站首页的真实影片卡片。"""
        return {'list': self._cards(self._get(self.site + '/videos'))}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        """根据原站分类标识与页码读取影片列表。"""
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        ext = self._parse_ext(extend)
        order = str(ext.get('o') or '').strip()
        tid = str(tid or 'all')

        # ② 二级分类(具体标签) → 直接出内容
        if tid.startswith(TAG_ITEM_PREFIX):
            return self._list_tag(self._tid_tag(tid), page, order, tid)

        # ① 一级标签父组 → 返回二级分类清单(class) + 首个子标签内容(list)
        if tid.startswith(TAG_GROUP_PREFIX) or tid == TAG_CLASS:
            key = tid[len(TAG_GROUP_PREFIX):] if tid.startswith(TAG_GROUP_PREFIX) else 'hot'
            items = self._tag_tree().get(key) or []
            if not items:
                items = [{'name': n, 'count': 0} for n in FALLBACK_TAGS]

            # 若壳走 filters 模式并已选中某个标签, 直接按它出内容
            picked = ''
            for k in sorted(ext.keys()):
                if k.startswith('tag') and str(ext[k]).strip():
                    picked = str(ext[k]).strip()
                    break
            if picked:
                return self._list_tag(picked, page, order, self._tag_tid(picked))

            sub = [{'type_id': self._tag_tid(t['name']),
                    'type_name': self._tag_label(t)} for t in items]
            res = self._list_tag(items[0]['name'], page, order, self._tag_tid(items[0]['name']))
            res['class'] = sub                      # 支持下钻的壳显示二级分类
            res['filters'] = {c['type_id']: [self._sort_row()] for c in sub}
            return res

        return self._list_videos('' if tid in ('all', '') else tid, page, order)

    def _list_videos(self, slug, page, order):
        """读取原站视频分类的指定页。"""
        path = '/videos' if not slug else '/videos/' + slug
        qs = {}
        if order:
            qs['o'] = order
        if page > 1:
            qs['page'] = page
        url = self.site + path + (('?' + urllib.parse.urlencode(qs)) if qs else '')
        html = self._get(url)
        total = self._total(html)
        pc = self._pagecount(html)
        if pc and page > pc:
            # 越界页站点会一直回最后一页(实测 sm 25 部, page 3/4/5 全等于 page 2)
            # 直接返回空, 否则壳会把同一批卡片重复追加
            return {'list': [], 'page': page, 'pagecount': pc,
                    'limit': 16, 'total': total}
        vods = self._dedup('cat:%s:%s' % (slug or 'all', order), page, self._cards(html))
        for item in vods:
            item['type_id'] = slug or 'all'
        return self.pageResult(vods, page, pc or None, 16, total or None)

    def _dedup(self, scope, page, vods):
        """搜索/标签端点存在服务端 offset 漂移(实测相邻页重叠 5~7 条),
        分类端点无此问题。只过滤"更早页出现过的 id", 并保证同一页重复请求幂等
        (壳会反复请求同页)。"""
        pages = self._seen.setdefault(scope, {})
        raw = [v['vod_id'] for v in vods]
        pages[page] = raw
        earlier = set()
        for p, ids in pages.items():
            if p < page:
                earlier.update(ids)
        out, seen = [], set()
        for v in vods:
            i = v['vod_id']
            if i in earlier or i in seen:
                continue
            seen.add(i)
            out.append(v)
        return out

    def _list_tag(self, kw, page, order, category_id='all'):
        """标签没有独立列表路由, 复用搜索端点: 路径段固定 /1, 真分页靠 ?page=N。"""
        url = '%s/search/videos/%s/1' % (self.site, urllib.parse.quote(kw, safe=''))
        qs = {}
        if order:
            qs['o'] = order
        if page > 1:
            qs['page'] = page
        if qs:
            url += '?' + urllib.parse.urlencode(qs)
        html = self._get(url, cache=False)
        total = self._total(html)
        pc = self._pagecount(html)
        if pc and page > pc:
            return {'list': [], 'page': page, 'pagecount': pc,
                    'limit': 16, 'total': total}
        vods = self._dedup('kw:%s:%s' % (kw, order), page, self._cards(html))
        for item in vods:
            item['type_id'] = category_id
        return self.pageResult(vods, page, pc or None, 16, total or None)

    def detailContent(self, ids):
        """读取真实影片详情和原站发布的分集线路。"""
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids).strip()
        vid = re.sub(r'\D', '', vid) or '0'
        html = self._get(self.site + '/video/' + vid, cache=False)

        title = re.search(r'<div class="well-filters video-title"[^>]*>\s*<h1>([\s\S]*?)</h1>', html) \
            or re.search(r'<h1>([\s\S]*?)</h1>', html)
        name = self._clean(title.group(1)) if title else ''
        if not name:
            return self.sourceFailure('源站详情未返回真实标题')

        pic = ''
        mp = re.search(r'itemprop="thumbnailUrl" content="([^"]+)"', html) \
            or re.search(r'poster="([^"]+)"', html)
        if mp:
            pic = mp.group(1)

        desc = ''
        md = re.search(r'class="[^"]*video-description"[^>]*>\s*<p>([\s\S]*?)</p>', html)
        if md:
            desc = self._clean(md.group(1))

        # 标签只取详情页 video-tags 区块(侧栏的搜索趋势不属于本片)
        tags = []
        mt = re.search(r'class="[^"]*video-tags[^"]*"[\s\S]*?</section>', html)
        if mt:
            tags = [self._clean(x) for x in re.findall(r'class="tag"[^>]*>([^<]+)<', mt.group(0))]

        # 详情页只有 video-tags 区块, 没有本片分类锚点
        # (页面里的 /videos/ 链接全部来自顶部导航, 不能当作影片归属分类)

        remarks = ''
        mdur = re.search(r'itemprop="duration" content="PT(?:(\d+)H)?(\d+)M(\d+)S"', html)
        if mdur:
            h, mi, se = mdur.group(1), mdur.group(2), mdur.group(3)
            remarks = ('%s:%02d:%02d' % (h, int(mi), int(se))) if h \
                else ('%02d:%02d' % (int(mi), int(se)))

        year = ''
        my = re.search(r'itemprop="uploadDate"[^>]*datetime="(\d{4})', html) \
            or re.search(r'datetime="(\d{4})-', html)
        if my:
            year = my.group(1)

        flags, urls = [], []
        for label, _u in self._sources(html):
            flags.append(label)
            # 只交出稳定的 vod_id, 真正的播放地址在 playerContent 里实时解析
            urls.append('%s$%s' % (remarks or '正片', vid))
        if not flags:
            flags, urls = ['AVJOY'], ['%s$%s' % (remarks or '正片', vid)]

        return {'list': [{
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': pic,
            'vod_year': year,
            'vod_remarks': remarks,
            'vod_tag': ','.join(tags),
            'vod_content': desc or name,
            'vod_play_from': '$$$'.join(flags),
            'vod_play_url': '$$$'.join(urls),
        }]}

    def searchContent(self, key, quick=False, pg=1):
        """按照关键词和实际页码读取原站搜索结果。"""
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        kw = str(key or '').strip()
        if not kw:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 16, 'total': 0}
        return self._list_tag(kw, page, '')

    def playerContent(self, flag, id, vipFlags=None):
        """仅返回详情页真实 source 地址，网页回退使用 parse=1。"""
        value = str(id or "").split("$")[-1]
        if not value.isdigit():
            return self.sourceFailure("源影片标识无效")
        page_url = self.site + "/video/" + value
        html = self._get(page_url, cache=False)
        sources = self._sources(html)
        address = next((url for name, url in sources if name == str(flag)), "")
        if not address and sources:
            address = sources[0][1]
        return {"parse": 0 if address else 1, "playUrl": "", "url": urllib.parse.urljoin(self.site + "/", address) if address else page_url,
                "header": {"User-Agent": UA, "Referer": self.site + "/"}}

    # ---------- 播放源 ----------
    def _sources(self, html):
        """详情页 <source> 列表。同清晰度可能给两条路径:
        /video/ 为 H.264(兼容性最好), /av1/ 为 AV1(部分设备无法硬解)。H.264 优先。"""
        items = []
        for m in re.finditer(r'<source\b[^>]*>', html):
            tag = m.group(0)
            src = re.search(r'src=["\']([^"\']+)["\']', tag)
            if not src:
                continue
            u = src.group(1)
            lab = re.search(r'label=["\']([^"\']*)["\']', tag)
            res = re.search(r'res=["\']([^"\']*)["\']', tag)
            q = (lab.group(1) if lab else '') or (res.group(1) + 'p' if res else '') or '默認'
            items.append(('AV1' if '/av1/' in u else 'H264', q, u))
        out, seen = [], set()
        for codec in ('H264', 'AV1'):
            for c, q, u in items:
                if c != codec:
                    continue
                label = q if codec == 'H264' else ('%s AV1' % q)
                if label in seen:
                    continue
                seen.add(label)
                out.append((label, u))
        return out


# --------------------------------------------------------------------------
# selftest: python3 avjoy.py
# --------------------------------------------------------------------------
