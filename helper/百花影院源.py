# -*- coding: utf-8 -*-
# 百花影院 www.balidwipa.com — OK影视 / TvBox Python 源
# 基于 PyramidStore (CatVod) Spider 接口
# 站点类型：苹果CMS (MacCMS)
# v3: 性能优化 - 预编译正则 / 跳过被劫持首页 / 收紧搜索窗口 / 单次合并正则

import sys
sys.path.append('..')
from base.spider import Spider
import json
import re
from urllib.parse import quote, unquote, urljoin
from bs4 import BeautifulSoup


class Spider(Spider):

    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = '百花影院'

    HOST = "https://www.balidwipa.com"

    CATE = {
        "电影": "1",
        "电视剧": "2",
        "综艺": "3",
        "动漫": "4",
    }

    YEAR_FILTERS = [
        {"n": "全部", "v": ""},
        {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
        {"n": "2024", "v": "2024"}, {"n": "2023", "v": "2023"},
        {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
        {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"},
        {"n": "2018", "v": "2018"}, {"n": "2017", "v": "2017"},
        {"n": "2016", "v": "2016"}, {"n": "2015", "v": "2015"},
        {"n": "更早", "v": "more"},
    ]

    UA = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"

    # ── 预编译正则（类常量，避免每次调用重新编译） ──
    RE_LIST_TITLE = re.compile(r'href="/yiy/(\d+)\.html"[^>]*>(.*?)</a>', re.S)
    RE_IMG_SRC = re.compile(r'<img[^>]*(?:data-src|data-original|src)="(https?://[^"]*)"')
    RE_PLAY_TAB = re.compile(r'href="[^"]*#playlist(\d+)"[^>]*>\s*([^<]*)\s*</a>', re.S)
    RE_PLAY_EP = re.compile(r'href="/play/(\d+)-(\d+)-(\d+)\.html"[^>]*>\s*([^<]*)\s*</a>', re.S)
    RE_OG_IMAGE = re.compile(r'<meta[^>]*og:image[^>]*content="([^"]+)"', re.I)
    RE_OG_IMAGE2 = re.compile(r'<meta[^>]*content="([^"]+)"[^>]*og:image', re.I)
    RE_DOUBAO_IMG = re.compile(r'(?:src|data-src|data-original)="(https?://[^"]*doubaocdn[^"]*)"')
    RE_MEDIA_M3U8 = re.compile(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', re.I)
    RE_MEDIA_MP4 = re.compile(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', re.I)
    RE_PLAYER_AAAA = re.compile(r'player_aaaa\s*=\s*(\{.*?\})\s*[;<]', re.S)
    RE_PLAYER_DATA = re.compile(r'player_data\s*=\s*(\{.*?\})\s*[;<]', re.S)
    RE_H1 = re.compile(r'<h1[^>]*>(.*?)</h1>', re.S)
    RE_TITLE_TAG = re.compile(r'<title>([^<]*)</title>')
    RE_BH_TYPE = re.compile(r'href="/bh/\d+\.html"[^>]*>([^<]+)</a>')
    RE_YEAR = re.compile(r'year=(\d+)')
    RE_AREA = re.compile(r'area=([^&"]+)')
    RE_SCORE = re.compile(r'(\d+\.?\d*)\s*分')
    REM_TAG = re.compile(r'<[^>]+>')
    RE_A_LINK = re.compile(r'>([^<]+)</a>')

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '百花影院'

    def init(self, extend=""):
        """保留本源无需额外配置的初始化入口。"""
        pass

    # ════════════ 首页 ════════════

    def homeContent(self, filter):
        """返回原站分类及可用筛选条件。"""
        result = {}
        classes = [{'type_name': n, 'type_id': self.CATE[n]} for n in self.CATE]
        result['class'] = classes
        if filter:
            filters = {}
            for name, cid in self.CATE.items():
                filters[cid] = [{"key": "year", "name": "年份", "value": self.YEAR_FILTERS}]
            result['filters'] = filters
        return result

    def homeVideoContent(self):
        # 首页已被劫持为赌博站，直接请求分类页（省 1 次 HTTP 请求）
        """读取原站首页推荐影片。"""
        result = {'list': []}
        try:
            html = self._fetch_html(self.HOST + "/bh/1.html")
            videos = self._parse_list_html(html)
            result = {'list': videos[:30]}
        except:
            pass
        return result

    # ════════════ 分类列表 ════════════

    def categoryContent(self, tid, pg, filter, extend):
        """请求原站指定分类页，分页只采用页面实际提供的信息。"""
        page = max(1, int(pg or 1))
        year = str((extend or {}).get("year") or "")
        if year and year != "全部":
            url = "%s/search.php?searchtype=5&tid=%s&year=%s&order=time&page=%s" % (self.HOST, tid, quote(year), page)
        else:
            url = "%s/bh/%s%s.html" % (self.HOST, tid, "-%s" % page if page > 1 else "")
        html = self._fetch_html(url)
        items = self._parse_list_html(html)
        for item in items:
            item["type_id"] = str(tid)
        return self.pageResult(items, page, self._parse_pagecount(html) or None, 30)

    # ════════════ 详情 ════════════

    def detailContent(self, array):
        """只返回原站真实详情；解析异常不创建虚假的影片或分集。"""
        if not array:
            return {"list": []}
        try:
            result = self._detail_inner(array)
            for item in result.get("list", []):
                if not item.get("vod_name") or not item.get("vod_play_url"):
                    return self.sourceFailure("源站详情缺少标题或真实播放列表")
            return result
        except Exception:
            return self.sourceFailure("源站详情读取或解析失败")

    def _detail_inner(self, array):
        """读取并解析指定影片的真实详情。"""
        vod_id = str(array[0])
        html = self._fetch_html("{0}/yiy/{1}.html".format(self.HOST, vod_id))
        if not html or len(html) < 200:
            raise Exception("详情页获取失败")

        # 标题
        title = self.RE_H1.search(html)
        title = self.REM_TAG.sub('', title.group(1)).strip() if title else ""
        if not title:
            m = self.RE_TITLE_TAG.search(html)
            title = m.group(1).strip() if m else ""

        # 分类名 / 年份 / 地区 / 评分
        type_name = self._m(self.RE_BH_TYPE, html)
        year = self._m(self.RE_YEAR, html)
        m = self.RE_AREA.search(html)
        area = unquote(m.group(1)) if m else ""
        m = self.RE_SCORE.search(html)
        score = m.group(1) if m else ""

        # 导演 / 主演
        director = self._extract_links_by_keyword(html, '导演')
        actor = self._extract_links_by_keyword(html, '主演')

        # 简介
        content = ""
        m = re.search(r'(?:剧情|简介)[：:]\s*(.*?)(?:</p>|</div>|<br|###|<h)', html, re.S)
        if m:
            content = self.REM_TAG.sub('', m.group(1)).replace('&nbsp;', ' ').strip()
            if len(content) > 500:
                content = content[:500] + '...'

        # 封面图（优先 og:image，其次 doubaocdn，快速短路）
        pic = self._extract_detail_pic(html)

        # 备注
        remarks = ""
        m = re.search(r'更新[：:]\s*([0-9\-: ]+)', html)
        if m:
            remarks = m.group(1).strip()

        vod = {
            "vod_id": vod_id, "vod_name": title, "vod_pic": pic,
            "type_name": type_name, "vod_year": year, "vod_area": area,
            "vod_remarks": remarks, "vod_actor": actor, "vod_director": director,
            "vod_content": content, "vod_score": score,
        }
        vod["vod_play_from"], vod["vod_play_url"] = self._parse_play_list(html, vod_id)
        return {'list': [vod]}

    # ════════════ 搜索 ════════════

    def searchContent(self, key, quick, pg="1"):
        """将页码送到真实搜索接口，不重复返回第一页。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, 30, 0)
        url = "%s/search.php?searchword=%s&page=%s" % (self.HOST, quote(str(key)), page)
        html = self._fetch_html(url)
        return self.pageResult(self._parse_list_html(html), page, self._parse_pagecount(html) or None, 30)

    # ════════════ 播放解析 ════════════

    def playerContent(self, flag, id, vipFlags):
        """解析所选真实分集的播放地址。"""
        result = {"parse": 1, "url": "", "header": ""}
        try:
            parts = id.split("___")
            if len(parts) < 3:
                return {"parse": 0, "url": id, "header": ""}
            vod_id, sid, nid = parts[0], parts[1], parts[2]

            play_url = "{0}/play/{1}-{2}-{3}.html".format(self.HOST, vod_id, sid, nid)
            html = self._fetch_html(play_url)

            # 1. MacCMS player_aaaa
            player_json = self._extract_player_data(html)
            if player_json and player_json.get('url', ''):
                real_url = player_json['url']
                encrypt = player_json.get('encrypt', 0)
                if encrypt == 1:
                    real_url = unquote(real_url)
                elif encrypt == 2:
                    real_url = self._aes_decrypt(real_url, html)
                result = {"parse": 0, "playUrl": "", "url": real_url,
                          "header": json.dumps(self._play_header())}
            else:
                # 2. 直接提取媒体 URL
                media_url = self._extract_media_url(html)
                if media_url:
                    result = {"parse": 0, "url": media_url,
                              "header": json.dumps(self._play_header())}
                else:
                    # 3. 嗅探播放页
                    result = {"parse": 1, "url": play_url, "header": ""}
        except:
            pass
        return result

    # ════════════ 辅助：网络 ════════════

    def _headers(self):
        """生成本源页面请求头。"""
        return {
            "User-Agent": self.UA,
            "Referer": self.HOST + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _play_header(self):
        """生成本源媒体请求头。"""
        return {"User-Agent": self.UA, "Referer": self.HOST + "/"}

    def _fetch_html(self, url):
        """读取原站 HTML，网络或 HTTP 错误不能伪装为空目录。"""
        try:
            response = self.fetch(url, headers=self._headers(), timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            if not response.text.strip():
                return self.sourceFailure("源站页面为空")
            return response.text
        except Exception:
            return self.sourceFailure("源站页面读取失败")

    # ════════════ 辅助：列表解析（性能关键路径） ════════════

    def _parse_list_html(self, html):
        """仅解析主影片卡片，排除页面顶部热搜与导航中的重复推荐。"""
        document = BeautifulSoup(html, "lxml")
        videos = []
        seen = set()
        # 关键变量：真实卡片容器可同时覆盖分类与搜索页，避免扫描整页链接。
        for card in document.select(".module-list .module-item"):
            anchor = card.select_one(".module-item-titlebox a[href]")
            if not anchor:
                continue
            match = re.fullmatch(r"/yiy/(\d+)\.html", anchor.get("href", ""))
            name = anchor.get_text(" ", strip=True)
            if not match or not name or match.group(1) in seen:
                continue
            vid = match.group(1)
            seen.add(vid)
            image = card.select_one("img")
            pic = ""
            if image:
                pic = image.get("data-original") or image.get("data-src") or image.get("src") or ""
            videos.append({"vod_id": vid, "vod_name": name,
                           "vod_pic": urljoin(self.HOST + "/", pic) if pic else "",
                           "vod_remarks": ""})
        return videos

    def _parse_pagecount(self, html):
        """读取原站页面实际分页链接。"""
        pages = re.findall(r'/bh/\d+-(\d+)\.html', html)
        if pages:
            nums = [int(p) for p in pages if int(p) > 0]
            if nums:
                return max(nums)
        pages2 = re.findall(r'page=(\d+)', html)
        if pages2:
            nums = [int(p) for p in pages2 if int(p) > 0]
            if nums:
                return max(nums)
        return 0

    # ════════════ 辅助：详情页解析 ════════════

    def _extract_detail_pic(self, html):
        """快速短路：og:image → doubaocdn → 任意非图标 img"""
        m = self.RE_OG_IMAGE.search(html) or self.RE_OG_IMAGE2.search(html)
        if m:
            return m.group(1)
        m = self.RE_DOUBAO_IMG.search(html)
        if m:
            return m.group(1)
        m = re.search(r'url\((https?://[^)]*doubaocdn[^)]*)\)', html)
        if m:
            return m.group(1)
        for m in re.finditer(r'<img[^>]*src="(https?://[^"]*)"', html):
            src = m.group(1)
            if not re.search(r'(icon|logo|favicon|avatar|loading|placeholder|blank)', src, re.I):
                return src
        return ""

    def _parse_play_list(self, html, vod_id):
        """按原站线路容器读取目标影片分集，同一真实播放标识仅保留一次。"""
        document = BeautifulSoup(html, "lxml")
        tab_names = {tab_id: name.strip() for tab_id, name in self.RE_PLAY_TAB.findall(html)}
        groups = {}
        names = {}
        seen = set()
        # 关键变量：播放列表容器决定线路归属，不能把播放按钮和相关推荐混进剧集。
        for container in document.select('div[id^="playlist"]'):
            tab_id = str(container.get("id", ""))[len("playlist"):]
            for anchor in container.select('a[href]'):
                match = re.fullmatch(r"/play/(\d+)-(\d+)-(\d+)\.html", anchor.get("href", ""))
                if not match or match.group(1) != str(vod_id):
                    continue
                identity = match.groups()
                if identity in seen:
                    continue
                seen.add(identity)
                _, sid, nid = identity
                episode_name = anchor.get_text(" ", strip=True) or ("第%s集" % (int(nid) + 1))
                groups.setdefault(sid, []).append("%s$%s___%s___%s" % (episode_name, vod_id, sid, nid))
                names[sid] = tab_names.get(tab_id) or ("线路" + sid)
        if not groups:
            return "", ""
        return "$$$".join(names[sid] for sid in groups), "$$$".join("#".join(items) for items in groups.values())

    def _extract_links_by_keyword(self, html, keyword):
        """位置法提取导演/主演，收紧块大小到 1500 字符"""
        idx = html.find(keyword)
        if idx == -1:
            idx = html.find(keyword.replace('：', ':'))
        if idx == -1:
            return ""

        chunk = html[idx: idx + 1500]
        stop_words = ['主演', '导演', '上映', '更新', '状态', '语言',
                      '编剧', '类型', '地区', '年份', '频道', '剧情',
                      '简介', '播放', '专辑', '豆瓣']
        stop_words = [w for w in stop_words if w != keyword]

        kw_len = len(keyword)
        min_pos = len(chunk)
        for sw in stop_words:
            pos = chunk.find(sw, kw_len)
            if pos != -1 and pos < min_pos:
                min_pos = pos

        block = chunk[:min_pos]
        names = self.RE_A_LINK.findall(block)
        return ' '.join(n.strip() for n in names if n.strip() and n.strip() != '/')

    # ════════════ 辅助：播放器 ════════════

    def _extract_player_data(self, html):
        """读取播放器公开配置数据。"""
        for pat in (self.RE_PLAYER_AAAA, self.RE_PLAYER_DATA):
            m = pat.search(html)
            if m:
                try:
                    return json.loads(m.group(1))
                except:
                    pass
        m = re.search(r'(player_\w+)\s*=\s*(\{[^}]+\})', html, re.S)
        if m:
            try:
                return json.loads(m.group(2))
            except:
                pass
        return {}

    def _extract_media_url(self, html):
        """从页面中读取明确的媒体地址。"""
        m = self.RE_MEDIA_M3U8.search(html) or self.RE_MEDIA_MP4.search(html)
        if not m:
            m = re.search(r'(https?://[^\s"\'<>]+\.flv[^\s"\'<>]*)', html, re.I)
        return m.group(1) if m else ""

    def _aes_decrypt(self, encrypted, html):
        """按原站播放器公开算法解码播放地址。"""
        try:
            key_match = re.search(r'key\s*[:=]\s*["\']([A-Za-z0-9]{16})["\']', html)
            key = key_match.group(1) if key_match else "28fd7d0f7dac4156"
            from Crypto.Cipher import AES
            import base64
            cipher = AES.new(key.encode(), AES.MODE_ECB)
            decrypted = cipher.decrypt(base64.b64decode(encrypted))
            pad = decrypted[-1]
            return decrypted[:-pad].decode('utf-8', errors='ignore')
        except:
            return encrypted

    # ════════════ 通用 ════════════

    def _m(self, regex, text):
        """正则取 group(1)，预编译版"""
        m = regex.search(text)
        return m.group(1) if m else ""

    def isVideoFormat(self, url):
        """判断地址是否为本源识别的媒体格式。"""
        pass

    def manualVideoCheck(self):
        """返回本源是否要求额外格式检查。"""
        pass

    def localProxy(self, param):
        """本源没有额外代理内容，不返回伪造的空视频。"""
        return [404, "text/plain; charset=utf-8", b"not found"]
