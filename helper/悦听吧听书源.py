# -*- coding: utf-8 -*-
"""悦听吧原生听书源：列表保存摘要，章节时长未知，播放时只解析当前章。"""

import base64
import functools
import hashlib
import html
import json
import math
import random
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from base.spider import Spider as BaseSpider


class _RateLimited(Exception):
    """限流只携带可公开的重试秒数，不含地址、凭据或原站正文。"""

    def __init__(self, retry_after):
        """将宿主可识别的等待范围限制为一秒至一小时。"""
        super().__init__("悦听吧请求较频繁，请稍后重试")
        self.retry_after = max(1, min(3600, int(math.ceil(retry_after))))


def _public_result(method):
    """限流转换为明确的小型错误对象；其他解析和网络错误继续由宿主处理。"""
    @functools.wraps(method)
    def wrapped(*args, **kwargs):
        """源调用不得以空列表或网页解析标记掩盖实际请求失败。"""
        try:
            return method(*args, **kwargs)
        except _RateLimited as error:
            return {"cloud_helper_error": {"code": "rate_limited", "retryAfterSeconds": error.retry_after}}
    return wrapped


class Spider(BaseSpider):
    """复用悦听吧公开目录和单章接口，不建立全书时间轴、不探测章节时长。"""

    cloud_helper_media_type = "audiobook"
    display_name = "悦听吧听书"
    SITE = "http://www.yuetingba.cn"
    TRACK_PAGE_SIZE = 200
    SEARCH_INTERVAL_SECONDS = 10
    RUNTIME_CACHE_SECONDS = 300
    UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    CATEGORIES = (
        ("1", "玄幻"), ("4", "都市"), ("2", "历史"), ("6", "名著"),
        ("7", "女频"), ("5", "科幻"), ("3", "武侠"), ("a", "评书"),
        ("8", "社科"), ("9", "儿童"),
    )
    # 关键变量：原站播放器公开的服务器配置加密参数，不是用户账号或登录凭据。
    _ASSL_MARKER = "xMiP5W1DHBxC5PwQ5oj5QfRn0tsT5UBk"
    _ASSL_KEY = "le95G3hnFDJsBE+1/v9eYw=="
    _ASSL_IV = "IvswQFEUdKYf+d1wKpYLTg=="

    def __init__(self):
        """源实例只保存匿名请求会话、限流时间和少量非音频地址的服务器配置。"""
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.SITE + "/",
        })
        self._runtime_cache = {}
        self._cooldown_until = 0.0
        self._search_allowed_at = 0.0

    def init(self, extend=""):
        """导入和初始化不请求源站，也不读取任何作品目录。"""
        return ""

    def getName(self):
        """返回宿主可识别的独立中文插件名称。"""
        return "悦听吧听书"

    @_public_result
    def homeContent(self, filter=False):
        """提供原站现有分类，不用影视分类规则推断有声书类型。"""
        return {"class": [{"type_id": key, "type_name": name} for key, name in self.CATEGORIES], "filters": {}}

    @_public_result
    def homeVideoContent(self):
        """兼容 Spider 首页调用，只读取一页书籍摘要。"""
        return self._category("1", 1)

    @_public_result
    def categoryContent(self, tid, pg, filter=False, extend=None):
        """分类页只提取当前页已有摘要，不补作品详情或章节。"""
        return self._category(str(tid), self._page(pg))

    def _category(self, category_id, page):
        """分类身份来自原站导航，分页沿用真实分类路径。"""
        if category_id not in dict(self.CATEGORIES):
            raise RuntimeError("悦听吧分类不存在")
        page_html = self._request_text("/book/%s/%s" % (category_id, page))
        return self._list_result(page_html, page, category_id)

    @_public_result
    def searchContent(self, key, quick=False, pg="1"):
        """执行原站 pageIndex 实时搜索，限流直接返回重试秒数，不在宿主中休眠。"""
        page = self._page(pg)
        term = str(key or "").strip()
        if not term or len(term) > 200:
            raise RuntimeError("悦听吧搜索关键词须为一至二百个字符")
        now = time.monotonic()
        allowed_at = max(self._cooldown_until, self._search_allowed_at)
        if now < allowed_at:
            raise _RateLimited(allowed_at - now)
        self._search_allowed_at = now + self.SEARCH_INTERVAL_SECONDS
        page_html = self._request_text("/Search", {"type": "1", "name": term, "pageIndex": page})
        return self._list_result(page_html, page)

    @_public_result
    def detailContent(self, ids):
        """兼容详情只返回第一页章节；原生服务通过 audioChapterPage 继续读取。"""
        if not isinstance(ids, list) or not ids:
            raise RuntimeError("悦听吧详情缺少作品标识")
        return self._chapter_page(self._uuid(ids[0], "作品"), 1)

    @_public_result
    def audioChapterPage(self, book_id, page):
        """按原站每二百章的偏移取一页，章节只含永久身份和未知时长摘要。"""
        return self._chapter_page(self._uuid(book_id, "作品"), self._page(page))

    def _chapter_page(self, book_id, page):
        """校验书籍、偏移、目录页范围和节点身份，避免把不完整网页当成完整章节。"""
        offset = (page - 1) * self.TRACK_PAGE_SIZE
        page_html = self._request_text("/book/detail/%s/%s" % (book_id, offset))
        self._assert_page_identity(page_html, book_id, offset)
        soup = BeautifulSoup(page_html, "html.parser")
        book = self._book_detail(soup, book_id)
        directory = soup.select_one(".ting-list")
        if directory is None:
            raise RuntimeError("悦听吧作品没有返回章节目录")
        offsets = set()
        for link in directory.select(".nav-tabs a[href]"):
            match = re.fullmatch(r"/book/detail/(%s)/(\d+)/?" % self.UUID_PATTERN, urlsplit(link.get("href", "")).path, re.I)
            if match and match.group(1).lower() == book_id:
                offsets.add(int(match.group(2)))
        if not offsets or offset not in offsets or any(value % self.TRACK_PAGE_SIZE for value in offsets):
            raise RuntimeError("悦听吧章节页范围与请求不一致")
        expected_offsets = set(range(0, max(offsets) + 1, self.TRACK_PAGE_SIZE))
        if offsets != expected_offsets:
            raise RuntimeError("悦听吧章节分页目录不完整")
        page_count = max(offsets) // self.TRACK_PAGE_SIZE + 1
        total_text = self._detail_field(soup, "集数")
        total = int(total_text) if re.fullmatch(r"\d+", total_text) else None
        if total is not None and (total <= 0 or math.ceil(total / self.TRACK_PAGE_SIZE) != page_count):
            raise RuntimeError("悦听吧章节总数与分页范围不一致")
        tracks = []
        seen = set()
        for node in directory.select(".ting-list-content .ting-list-content-item"):
            node_id = str(node.get("id", ""))
            match = re.fullmatch(r"item_(%s)" % self.UUID_PATTERN, node_id, re.I)
            if not match:
                raise RuntimeError("悦听吧章节缺少永久标识")
            chapter_id = self._uuid(match.group(1), "章节")
            title_link = node.select_one("a[title][onclick]")
            if title_link is None:
                raise RuntimeError("悦听吧章节缺少标题或播放身份")
            called_id = re.fullmatch(r"\s*testFn\(['\"](%s)['\"]\)\s*;?\s*" % self.UUID_PATTERN, str(title_link.get("onclick", "")), re.I)
            if not called_id or called_id.group(1).lower() != chapter_id or chapter_id in seen:
                raise RuntimeError("悦听吧章节身份重复或不一致")
            seen.add(chapter_id)
            title = self._clean(title_link.get("title", ""))
            if not title:
                raise RuntimeError("悦听吧章节标题为空")
            tracks.append({"id": chapter_id, "title": title, "duration": None, "mimeType": "application/octet-stream",
                           "playbackId": "%s:%s" % (book_id, chapter_id), "flag": "yuetingba", "order": offset + len(tracks) + 1})
        if not tracks or len(tracks) > self.TRACK_PAGE_SIZE:
            raise RuntimeError("悦听吧章节页为空或超过二百章")
        expected_count = min(self.TRACK_PAGE_SIZE, total - offset) if total is not None else self.TRACK_PAGE_SIZE if page < page_count else len(tracks)
        if len(tracks) != expected_count:
            raise RuntimeError("悦听吧当前章节页尚未读取完整")
        # 关键变量：这里只缓存服务器配置，绝不缓存章节签名地址或音频字节。
        self._remember_runtime(book_id, page_html)
        book["vod_audio_tracks"] = tracks
        return {"list": [book], "page": page, "pagecount": page_count, "raw_count": len(tracks),
                "authoritative": False, "snapshot_id": None}

    @_public_result
    def audioChapterPlayback(self, book_id, chapter_id):
        """使用稳定书章身份直接取当前章 EFI，不枚举全书或读取其他章播放地址。"""
        return self._resolve_audio(self._uuid(book_id, "作品"), self._uuid(chapter_id, "章节"))

    @_public_result
    def playerContent(self, flag, id, vipFlags=None):
        """兼容旧播放器输入时仍校验书章二元组，不接受临时音频 URL。"""
        parts = str(id or "").split(":")
        if len(parts) != 2:
            raise RuntimeError("悦听吧播放输入缺少稳定书章标识")
        return self._resolve_audio(self._uuid(parts[0], "作品"), self._uuid(parts[1], "章节"))

    def _resolve_audio(self, book_id, chapter_id):
        """按当前章节 API 返回的原站参数生成短期公开音频地址，不持久化结果。"""
        response_text = self._request_text("/api/app/docs-listen/%s/ting-with-efi" % chapter_id)
        try:
            data = json.loads(response_text)
        except ValueError:
            raise RuntimeError("悦听吧章节接口没有返回有效 JSON") from None
        if not isinstance(data, dict) or self._uuid(data.get("id"), "章节") != chapter_id or self._uuid(data.get("bookId"), "作品") != book_id:
            raise RuntimeError("悦听吧章节接口返回的书章身份不一致")
        try:
            ting_no = int(data["tingNo"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("悦听吧章节接口缺少真实顺序") from None
        if ting_no <= 0:
            raise RuntimeError("悦听吧章节顺序无效")
        creation_time = str(data.get("creationTime") or "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", creation_time):
            raise RuntimeError("悦听吧章节创建时间格式无效")
        compact_time = re.sub(r"\D", "", creation_time).ljust(20, "0")[:20]
        plain_id = chapter_id.replace("-", "")
        key = bytes((ord(plain_id[index]) + int(compact_time[index % 20])) & 0xff for index in range(len(plain_id)))
        iv = bytes((ord(plain_id[index]) + int(compact_time[index - 1])) & 0xff for index in range(20, 4, -1))
        try:
            source_path = self._aes_decrypt(data.get("efi"), key, iv).decode("utf-8").strip()
        except (UnicodeError, ValueError, TypeError):
            raise RuntimeError("悦听吧当前章节音频参数无效") from None
        if not source_path.startswith("/") or source_path.startswith("//") or any(value in source_path for value in ("\r", "\n", "?", "#")):
            raise RuntimeError("悦听吧当前章节音频路径无效")
        filename = unquote(source_path.rstrip("/").rsplit("/", 1)[-1])
        if not filename or "/" in filename or "\\" in filename:
            raise RuntimeError("悦听吧音频文件名无效")
        runtime = self._book_runtime(book_id, ((ting_no - 1) // self.TRACK_PAGE_SIZE) * self.TRACK_PAGE_SIZE)
        server = self._pick_server(runtime["servers"], book_id)
        path = source_path
        if "_p" in server["Name"]:
            path = "/%s_%s/%s" % (runtime["py"], book_id, quote(filename, safe=""))
        elif "_b" in server["Name"]:
            path = "/myfiles/host/listen/booksdir/%s_%s/%s" % (runtime["py"], book_id, quote(filename, safe=""))
        expires = int(time.time()) + 600
        signature = hashlib.md5(("%s|%s|%s" % (filename, expires, self._ASSL_MARKER)).encode("utf-8")).hexdigest()
        host = "%s://%s:%s" % (server["Scheme"], server["Value"], server["Port"])
        audio_url = "%s%s?token=%s&expire=%s" % (host, path, signature, expires)
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime = {"mp3": "audio/mpeg", "mp4": "audio/mp4", "m4a": "audio/mp4", "m4b": "audio/mp4", "aac": "audio/aac",
                "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav"}.get(suffix, "application/octet-stream")
        return {"parse": 0, "url": audio_url, "mimeType": mime,
                "header": {"User-Agent": self.session.headers["User-Agent"], "Referer": self.SITE + "/"}}

    def _book_runtime(self, book_id, offset):
        """仅在小型配置缓存失效时读取当前章所在页，不抓取其他章节页。"""
        cached = self._runtime_cache.get(book_id)
        if cached and cached["expires_at"] > time.monotonic():
            return cached
        page_html = self._request_text("/book/detail/%s/%s" % (book_id, offset))
        self._assert_page_identity(page_html, book_id, offset)
        return self._remember_runtime(book_id, page_html)

    def _remember_runtime(self, book_id, page_html):
        """解码原站公开服务器配置，缓存最多十六本书且不保存生成后的播放地址。"""
        encrypted = self._js_var(page_html, "assl").replace(self._ASSL_MARKER, "")
        try:
            servers = json.loads(self._aes_decrypt(encrypted, base64.b64decode(self._ASSL_KEY), base64.b64decode(self._ASSL_IV)).decode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            raise RuntimeError("悦听吧音频服务器配置无效") from None
        if not isinstance(servers, list) or not servers or len(servers) > 100:
            raise RuntimeError("悦听吧没有返回有效音频服务器列表")
        py = self._js_var(page_html, "py")
        if not re.fullmatch(r"[0-9a-z_-]{1,64}", py, re.I):
            raise RuntimeError("悦听吧作品音频目录参数无效")
        now = time.monotonic()
        for key, value in list(self._runtime_cache.items()):
            if value["expires_at"] <= now:
                del self._runtime_cache[key]
        while len(self._runtime_cache) >= 16 and book_id not in self._runtime_cache:
            del self._runtime_cache[next(iter(self._runtime_cache))]
        cached = {"servers": servers, "py": py, "expires_at": now + self.RUNTIME_CACHE_SECONDS}
        self._runtime_cache[book_id] = cached
        return cached

    @staticmethod
    def _pick_server(servers, book_id):
        """按原站公开的专用分组或普通服务器权重选择，不改写服务器身份。"""
        available = [item for item in servers if isinstance(item, dict) and str(item.get("AsType")) == "1" and item.get("Type") == "A"]
        group = book_id.rsplit("-", 1)[-1]
        dedicated = [item for item in available if item.get("BookIds") and group in item["BookIds"]]
        if dedicated:
            available = dedicated
        else:
            available = [item for item in available if int(item.get("Ratio") or 0) > 0 and not str(item.get("BookIds") or "").strip()]
        if not available:
            raise RuntimeError("悦听吧当前没有可用音频服务器")
        weights = [max(0, int(item.get("Ratio") or 0)) for item in available]
        selected = random.choices(available, weights=weights if sum(weights) else None, k=1)[0]
        scheme = str(selected.get("Scheme") or "").lower()
        value = str(selected.get("Value") or "")
        port = int(selected.get("Port") or 0)
        if scheme not in ("http", "https") or not value or re.search(r"[\s/?#@\\]", value) or not 1 <= port <= 65535:
            raise RuntimeError("悦听吧音频服务器地址无效")
        return {**selected, "Name": str(selected.get("Name") or ""), "Scheme": scheme, "Value": value, "Port": port}

    def _request_text(self, path, params=None):
        """保留原站错误并正确解码 UTF-8；429 使用冷却时间而不占用 Python 执行队列。"""
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            raise _RateLimited(remaining)
        try:
            response = self.session.get(urljoin(self.SITE + "/", path.lstrip("/")), params=params, timeout=(10, 20))
        except requests.RequestException:
            raise RuntimeError("悦听吧网络请求失败，请稍后重试") from None
        if response.status_code == 429:
            retry_after = self._retry_after(response.headers.get("Retry-After"))
            self._cooldown_until = time.monotonic() + retry_after
            raise _RateLimited(retry_after)
        if response.status_code != 200:
            raise RuntimeError("悦听吧请求失败，HTTP %s" % response.status_code)
        if len(response.content) > 2 * 1024 * 1024:
            raise RuntimeError("悦听吧单页响应过大")
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response):
        """只依据 HTTP 或 HTML 明确字符集，缺省 UTF-8，不使用搜索页易误判的探测编码。"""
        charset = re.search(r"charset\s*=\s*['\"]?([a-z0-9._-]+)", response.headers.get("Content-Type", ""), re.I)
        if charset:
            encoding = charset.group(1)
        else:
            declaration = re.search(br"<meta\b[^>]*charset\s*=\s*['\"]?([a-z0-9._-]+)", response.content[:8192], re.I)
            encoding = declaration.group(1).decode("ascii") if declaration else "utf-8"
        if encoding.lower().replace("_", "-") in ("utf-8", "utf8"):
            encoding = "utf-8-sig"
        try:
            return response.content.decode(encoding)
        except (LookupError, UnicodeError):
            raise RuntimeError("悦听吧页面字符集无效，请稍后重试") from None

    @staticmethod
    def _retry_after(value):
        """同时接受 HTTP 秒数和日期，缺少限流提示时冷却三十秒。"""
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                seconds = parsedate_to_datetime(str(value)).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                seconds = 30
        if not math.isfinite(seconds):
            seconds = 30
        return max(1, min(3600, int(math.ceil(seconds))))

    def _list_result(self, page_html, page, category_id=None):
        """使用当前页面真实分页范围，解析失败不能伪装为搜索无结果。"""
        soup = BeautifulSoup(page_html, "html.parser")
        pagination = soup.select_one("#PageContent")
        if pagination is None:
            raise RuntimeError("悦听吧没有返回有效书籍列表页面")
        books = []
        seen = set()
        for block in soup.select(".section-box-list-item"):
            title_link = block.select_one(".box-list-item-text-title a[href]")
            if title_link is None:
                raise RuntimeError("悦听吧书籍摘要缺少标题链接")
            match = re.fullmatch(r"/book/detail/(%s)/\d+/?" % self.UUID_PATTERN, urlsplit(title_link.get("href", "")).path, re.I)
            if not match:
                raise RuntimeError("悦听吧书籍摘要缺少永久作品标识")
            book_id = self._uuid(match.group(1), "作品")
            if book_id in seen:
                continue
            seen.add(book_id)
            title = self._clean(title_link.get_text(" ", strip=True))
            if not title:
                raise RuntimeError("悦听吧书籍摘要标题为空")
            image = block.select_one(".box-list-item-img img[src]")
            overview = block.select_one(".box-list-item-text-intro")
            authors = []
            narrators = []
            for person in block.select(".box-list-item-text-autspeaker a[href]"):
                query = parse_qs(urlsplit(person.get("href", "")).query)
                name = self._clean(person.get_text(" ", strip=True))
                target = authors if query.get("type") == ["2"] else narrators if query.get("type") == ["3"] else None
                if target is not None and name and name not in target:
                    target.append(name)
            book = {"vod_id": book_id, "vod_name": title, "vod_pic": urljoin(self.SITE, image.get("src", "")) if image else "",
                    "vod_content": self._clean(overview.get_text("\n", strip=True)) if overview else "", "vod_author": authors, "vod_narrator": narrators}
            if category_id:
                book.update({"type_id": category_id, "type_name": dict(self.CATEGORIES)[category_id]})
            books.append(book)
        if len(books) > 200:
            raise RuntimeError("悦听吧书籍单页超过二百条")
        pagination_text = pagination.get_text(" ", strip=True)
        total_match = re.search(r"共\s*(\d+)\s*条", pagination_text)
        size_match = re.search(r"(\d+)\s*条\s*/\s*页", pagination_text)
        page_size = int(size_match.group(1)) if size_match else 10
        if page_size <= 0:
            raise RuntimeError("悦听吧列表分页大小无效")
        total = int(total_match.group(1)) if total_match else None
        page_count = max(1, math.ceil(total / page_size)) if total is not None else page
        for link in pagination.select("a[href]"):
            target = urlsplit(link.get("href", ""))
            number = parse_qs(target.query).get("pageIndex", [None])[0]
            if category_id:
                match = re.fullmatch(r"/book/%s/(\d+)/?" % re.escape(category_id), target.path, re.I)
                number = match.group(1) if match else None
            if number and str(number).isdigit():
                page_count = max(page_count, int(number))
        if total is not None and total > 0 and page <= page_count and not books:
            raise RuntimeError("悦听吧当前书籍列表尚未读取完整")
        result = {"list": books, "page": page, "pagecount": page_count, "limit": page_size, "raw_count": len(books)}
        if total is not None:
            result["total"] = total
        return result

    def _book_detail(self, soup, book_id):
        """详情仅提取当前作品的真实名称、作者、演播、分类和简介。"""
        title_node = soup.select_one(".books-detail-detail h1") or soup.select_one("h2.book-detail-title")
        title = self._clean(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title:
            raise RuntimeError("悦听吧作品详情缺少标题")
        image = soup.select_one(".books-detail-img img[src]")
        category_name = self._detail_field(soup, "分类")
        category_id = next((key for key, name in self.CATEGORIES if name == category_name), None)
        overview = ""
        for heading in soup.select("h4"):
            if self._clean(heading.get_text(" ", strip=True)).rstrip("：:") == "内容简介":
                content = heading.parent.find_next_sibling()
                overview = self._clean(content.get_text("\n", strip=True)) if content else ""
                break
        authors = self._detail_people(soup, "作者")
        narrators = self._detail_people(soup, "演播")
        result = {"vod_id": book_id, "vod_name": title, "vod_pic": urljoin(self.SITE, image.get("src", "")) if image else "",
                  "vod_author": authors, "vod_narrator": narrators, "vod_content": overview}
        if category_id:
            result.update({"type_id": category_id, "type_name": category_name, "vod_class": [category_name]})
        return result

    @staticmethod
    def _detail_field_node(soup, field):
        """详情字段按明确标签读取，标签内部空格不影响作者和集数字段。"""
        for label in soup.select(".text-desc-title"):
            name = re.sub(r"\s+", "", label.get_text(" ", strip=True)).rstrip("：:")
            if name == field:
                return label.find_next_sibling("span", class_="text-desc-content")
        return None

    def _detail_field(self, soup, field):
        """读取已找到字段的展示文字，不根据作品标题推断内容。"""
        node = self._detail_field_node(soup, field)
        return self._clean(node.get_text(" ", strip=True)) if node else ""

    def _detail_people(self, soup, field):
        """人名优先使用原站独立链接，避免把“多人演播”说明当成人名。"""
        node = self._detail_field_node(soup, field)
        if node is None:
            return []
        values = [self._clean(link.get_text(" ", strip=True)) for link in node.select("a")]
        if not values:
            value = self._clean(node.get_text(" ", strip=True))
            values = [value] if value else []
        return list(dict.fromkeys(value for value in values if value))

    def _assert_page_identity(self, page_html, book_id, offset):
        """根据原站播放器明确的书籍 UUID 和实际偏移校验，不能从页名制造身份。"""
        if self._uuid(self._js_var(page_html, "bookId"), "作品") != book_id or self._js_var(page_html, "skip") != str(offset):
            raise RuntimeError("悦听吧返回了其他作品或其他章节页")

    @staticmethod
    def _js_var(page_html, name):
        """只读取明确的字符串常量，不执行页面 JavaScript 或第三方广告代码。"""
        match = re.search(r"\b(?:var|let|const)\s+%s\s*=\s*['\"]([^'\"]*)['\"]" % re.escape(name), page_html)
        if not match:
            raise RuntimeError("悦听吧页面缺少必要播放器参数")
        return html.unescape(match.group(1))

    @staticmethod
    def _aes_decrypt(cipher_text, key, iv):
        """使用容器已有 AES 能力解析原站公开参数，严格校验 CBC 填充。"""
        if not isinstance(cipher_text, str):
            raise ValueError("音频加密参数无效")
        encrypted = base64.b64decode(re.sub(r"\s+", "", cipher_text), validate=True)
        try:
            from Crypto.Cipher import AES
            padded = AES.new(key, AES.MODE_CBC, iv).decrypt(encrypted)
        except ImportError:
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            except ImportError:
                raise RuntimeError("悦听吧需要服务环境已有的 pycryptodome 或 cryptography") from None
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(encrypted) + decryptor.finalize()
        if not padded:
            raise ValueError("音频参数为空")
        padding_size = padded[-1]
        if padding_size < 1 or padding_size > 16 or padded[-padding_size:] != bytes([padding_size]) * padding_size:
            raise ValueError("音频参数填充无效")
        return padded[:-padding_size]

    @classmethod
    def _uuid(cls, value, kind):
        """作品和章节直接使用原站永久 UUID，不使用临时地址或数组下标。"""
        text = str(value or "").strip()
        if not re.fullmatch(cls.UUID_PATTERN, text, re.I):
            raise RuntimeError("悦听吧%s永久标识无效" % kind)
        return text.lower()

    @staticmethod
    def _page(value):
        """页码从一开始，拒绝异常输入而非悄悄重新读取第一页。"""
        text = str(value)
        if not re.fullmatch(r"[1-9]\d{0,3}", text):
            raise RuntimeError("悦听吧页码无效")
        return int(text)

    @staticmethod
    def _clean(value):
        """清理原站已提取的文字，不处理或执行页面脚本。"""
        return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(str(value or ""))).strip()
