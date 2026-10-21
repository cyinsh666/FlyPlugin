# -*- coding: utf-8 -*-
# deb.sfyjs9.ink — 私房研究所 Python 源 (CatVod) Spider
# 站点类型: 自定义 CMS (008blacktwo_wtpl 模板)
# 特性: 无 JS Cookie 验证, 根路由, DPlayer + HLS 直链
# 原始域名 https://lcw.sfyjs7.beauty/cn/ 会 301 重定向到 https://deb.sfyjs9.ink/cn/
# 但所有视频/分类/搜索链接均为根级路径 (不含 /cn/)@猪猪

import sys
sys.path.append('..')
from base.spider import Spider
import json
import re
from urllib.parse import quote, unquote
from concurrent.futures import ThreadPoolExecutor

try:
    from Crypto.Cipher import AES
    import base64
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False


def _log(msg):
    """仅记录源调用事件，避免输出签名地址、Cookie 或密钥。"""
    sys.stderr.write(json.dumps({"日志关键字": "codex-python-source", "事件": "源内部日志", "源名称": "私房研究所"}, ensure_ascii=False) + "\n")


class Spider(Spider):

    # 站点主域名 (实际服务器, lcw.sfyjs7.beauty 会 301 到这里)
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = '私房研究所'

    HOST = "https://deb.sfyjs9.ink"

    # ── 一级分类 ──
    CATE = {
        "女神学生": "21",
        "美女直播": "22",
        "人妻系列": "23",
        "强奸乱伦": "24",
        "自拍偷拍": "25",
        "制服诱惑": "26",
        "巨乳系列": "27",
        "自慰系列": "28",
        "国产视频": "29",
        "无码视频": "30",
        "有码视频": "31",
        "中文字幕": "32",
        "日韩精品": "33",
        "欧美精品": "34",
        "动漫精品": "35",
        "三级伦理": "36",
    }

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    # ── 预编译正则 ──

    # 列表页视频卡片: <a class="display d-block" href="/{vid}.html"><img class="w-100" src="{pic}" /><small class="layer">{date}</small></a><a class="title ..." href="/{vid}.html">{title}</a>
    RE_LIST_CARD = re.compile(
        r'<a\s+class="display\s+d-block"\s+href="/(\d+)\.html"\s*>'
        r'\s*<img\s+class="w-100"\s+src="([^"]*)"\s*/?>'
        r'\s*<small\s+class="layer">([^<]*)</small>'
        r'\s*</a>'
        r'\s*<a\s+class="title[^"]*"\s+href="/\1\.html">([^<]*)</a>',
        re.S
    )

    # 分页: 共{N}页
    RE_TOTAL_PAGE = re.compile(r'共(\d+)页')

    # 详情页标题
    RE_DETAIL_TITLE = re.compile(r'<h5\s+class="container-title[^"]*"[^>]*>(.*?)</h5>', re.S)

    # m3u8 直链: const rawUrl = 'https://....m3u8'
    RE_RAW_URL = re.compile(r"const\s+rawUrl\s*=\s*'([^']+)'")

    # 通用 m3u8/mp4 提取 (备用)
    RE_MEDIA_M3U8 = re.compile(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', re.I)
    RE_MEDIA_MP4 = re.compile(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', re.I)

    # og:image (备用封面)
    RE_OG_IMAGE = re.compile(r'<meta[^>]*og:image[^>]*content="([^"]+)"', re.I)

    # 详情页描述 (备用)
    RE_DETAIL_DESC = re.compile(r'<meta\s+name="description"\s+content="([^"]*)"', re.I)

    # 详情页分类
    RE_DETAIL_CATE = re.compile(r'<meta\s+name="keywords"\s+content="([^"]*)"', re.I)

    # 去标签
    RE_REM_TAG = re.compile(r'<[^>]+>')

    def __init__(self):
        """缓存由原站详情确认的分类，搜索结果无需猜测归属。"""
        self._category_cache = {}

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '私房研究所'

    def isVideoFormat(self, url):
        """保留格式检查入口，最终格式由真实媒体结果判断。"""
        pass

    def manualVideoCheck(self):
        """保留手动检查入口，不触发媒体下载。"""
        pass

    def localProxy(self, param):
        """本源无额外代理，明确返回不存在。"""
        return [404, "text/plain; charset=utf-8", b"not found"]

    def init(self, extend=""):
        """初始化独立内容源配置，保留原站正常访问规则。"""
        pass

    # ════════════ 请求辅助 ════════════

    def _headers(self):
        """生成访问原站页面所需的公开请求头。"""
        return {
            "User-Agent": self.UA,
            "Referer": self.HOST + "/cn/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _play_header(self):
        """生成原站媒体请求所需的公开请求头。"""
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

    # ════════════ 首页 ════════════

    def homeContent(self, filter):
        """返回原站分类目录和可用筛选项。"""
        result = {}
        classes = [{'type_name': n, 'type_id': self.CATE[n]} for n in self.CATE]
        result['class'] = classes
        if filter:
            result['filters'] = {}
        return result

    def homeVideoContent(self):
        """读取原站首页的真实影片卡片。"""
        result = {'list': []}
        try:
            html = self._fetch_html(self.HOST + "/cn/")
            if not html:
                _log("首页HTML为空")
                return result
            videos = self._parse_list_html(html)
            _log("首页解析到 " + str(len(videos)) + " 条")
            result = {'list': videos[:30]}
        except Exception as e:
            _log("首页异常: " + str(e))
        return result

    # ════════════ 分类列表 ════════════

    def categoryContent(self, tid, pg, filter, extend):
        """按原站分类和页码浏览，不使用固定的虚假总量。"""
        page = max(1, int(pg or 1))
        url = "%s/vodtype/%s%s.html" % (self.HOST, tid, "-%s" % page if page > 1 else "")
        html = self._fetch_html(url)
        items = self._parse_list_html(html)
        for item in items:
            item["type_id"] = str(tid)
        return self.pageResult(items, page, self._parse_pagecount(html) or None, 60)

    # ════════════ 详情 (本站详情页即播放页) ════════════

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
        """读取并解析本作品真实详情页面。"""
        vod_id = str(array[0])
        url = "{0}/{1}.html".format(self.HOST, vod_id)
        html = self._fetch_html(url)
        if not html or len(html) < 200:
            raise Exception("详情页获取失败")

        # 标题
        title = self._m(self.RE_DETAIL_TITLE, html)
        if not title:
            # 从 <title> 标签提取 (格式: 正在播放{title})
            t = re.search(r'<title>([^<]*)</title>', html, re.S)
            if t:
                title = t.group(1).replace('正在播放', '').strip()
        title = self.RE_REM_TAG.sub('', title).strip() if title else ""

        # 封面: 从推荐列表中找当前 vid 的图片, 否则用 og:image
        pic = self._find_pic_for_vid(html, vod_id)
        if not pic:
            pic = self._m(self.RE_OG_IMAGE, html)

        # 描述
        content = ""
        m = self.RE_DETAIL_DESC.search(html)
        if m:
            content = m.group(1).replace('_', ' ').strip()
            if len(content) > 500:
                content = content[:500] + '...'

        # 分类
        type_name = self._category_from_detail(html)
        if type_name:
            self._category_cache[vod_id] = self.CATE[type_name]

        # 提取 m3u8 直链
        play_url = self._extract_play_url(html, vod_id)

        vod = {
            "vod_id": vod_id, "vod_name": title, "vod_pic": pic,
            "type_name": type_name, "vod_year": "", "vod_area": "",
            "vod_remarks": "", "vod_actor": "", "vod_director": "",
            "vod_content": content,
            "vod_play_from": "默认线路",
            "vod_play_url": "播放$" + play_url,
        }
        if type_name:
            vod["type_id"] = self.CATE[type_name]
        _log("详情完成: " + title + " | play_url长度: " + str(len(play_url)))
        return {'list': [vod]}

    # ════════════ 搜索 ════════════

    def searchContent(self, key, quick, pg="1"):
        """读取源站实际搜索分页，不将失败包装为空搜索成功。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, 60, 0)
        # 关键变量：使用原站搜索表单的 wd/page，不能将关键词拼成未定义的详情路由。
        path = "/s/index.html?wd=%s&page=%s" % (quote(str(key)), page)
        html = self._fetch_html(self.HOST + path)
        items = self._parse_list_html(html)
        # 关键变量：卡片没有类别字段；有限并发读取真实详情，避免全局搜索丢失分类。
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(self._fill_search_category, items))
        return self.pageResult(items, page, self._parse_pagecount(html) or None, 48)

    def _category_from_detail(self, html):
        """仅匹配原站详情 keywords 中明确发布的分类，不从标题推断。"""
        match = self.RE_DETAIL_CATE.search(html)
        values = [value.strip() for value in match.group(1).split(",")] if match else []
        return next((value for value in values if value in self.CATE), "")

    def _fill_search_category(self, item):
        """补充一条真实搜索结果的原站分类并按稳定作品 ID 缓存。"""
        video_id = str(item["vod_id"])
        category_id = self._category_cache.get(video_id)
        if not category_id:
            html = self._fetch_html("%s/%s.html" % (self.HOST, video_id))
            category_name = self._category_from_detail(html)
            if category_name:
                category_id = self.CATE[category_name]
                self._category_cache[video_id] = category_id
        if category_id:
            item["type_id"] = category_id

    # ════════════ 播放解析 ════════════

    def playerContent(self, flag, id, vipFlags):
        # id 可能是 m3u8 直链, 也可能是 vod_id (fallback)
        """按真实分集标识读取媒体地址，网页交由宿主解析。"""
        try:
            # 如果 id 已经是 URL, 直接返回
            if id.startswith("http"):
                _log("播放直链: " + id[:80] + "...")
                return {
                    "parse": 0, "playUrl": "", "url": id,
                    "header": json.dumps(self._play_header())
                }

            # 否则作为 vod_id 重新获取页面提取 m3u8
            vod_id = id
            url = "{0}/{1}.html".format(self.HOST, vod_id)
            _log("播放URL(重新解析): " + url)
            html = self._fetch_html(url)
            if not html:
                return {"parse": 1, "url": url, "header": ""}

            play_url = self._extract_play_url(html, vod_id)
            if play_url and play_url.startswith("http"):
                _log("播放解析成功: " + play_url[:80] + "...")
                return {
                    "parse": 0, "playUrl": "", "url": play_url,
                    "header": json.dumps(self._play_header())
                }

            _log("未找到播放链接, fallback到网页解析")
        except Exception as e:
            _log("播放异常: " + str(e))

        return {"parse": 1, "url": "{0}/{1}.html".format(self.HOST, id), "header": ""}

    # ════════════ 辅助: 列表解析 ════════════

    def _parse_list_html(self, html):
        """从原站列表页面读取真实作品卡片。"""
        videos = []
        seen = set()

        for m in self.RE_LIST_CARD.finditer(html):
            vid = m.group(1)
            if vid in seen:
                continue
            pic = m.group(2).strip()
            date = m.group(3).strip()
            name = m.group(4).strip()

            if not name:
                continue
            seen.add(vid)
            videos.append({
                "vod_id": str(vid),
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": date,
            })

        return videos

    def _parse_pagecount(self, html):
        """从页面公布的分页文字读取总页数。"""
        m = self.RE_TOTAL_PAGE.search(html)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        return 0

    # ════════════ 辅助: 播放链接提取 ════════════

    def _extract_play_url(self, html, vod_id):
        """从详情页 HTML 提取 m3u8 直链"""
        # 优先: const rawUrl = '....m3u8'
        m = self.RE_RAW_URL.search(html)
        if m:
            url = m.group(1).strip()
            if url.startswith("http"):
                return url

        # 备用: 通用 m3u8 正则
        m = self.RE_MEDIA_M3U8.search(html)
        if m:
            url = m.group(1).strip()
            # 清理尾部可能的 JS 残留
            url = re.split(r"[';]", url)[0]
            if url.startswith("http"):
                return url

        # 备用: mp4
        m = self.RE_MEDIA_MP4.search(html)
        if m:
            url = m.group(1).strip()
            url = re.split(r"[';]", url)[0]
            if url.startswith("http"):
                return url

        # 最终 fallback: 返回 vod_id, playerContent 会重新解析
        return vod_id

    # ════════════ 辅助: 在详情页中查找当前 vid 的封面图 ════════════

    def _find_pic_for_vid(self, html, vod_id):
        """在详情页推荐列表中查找与 vod_id 对应的封面图"""
        pattern = re.compile(
            r'<a\s+class="display\s+d-block"\s+href="/' + re.escape(vod_id) + r'\.html"\s*>'
            r'\s*<img\s+class="w-100"\s+src="([^"]*)"',
            re.S
        )
        m = pattern.search(html)
        return m.group(1).strip() if m else ""

    # ════════════ 通用 ════════════

    def _m(self, regex, text):
        """只返回正则实际匹配到的首个字段。"""
        m = regex.search(text)
        return m.group(1) if m else ""
