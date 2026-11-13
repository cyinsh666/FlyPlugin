# coding=utf-8
"""黄果访客视频源。接口及封面格式参考 HGhubX（66a2cbb），限非商业使用。

PolyForm Noncommercial License 1.0.0: https://polyformproject.org/licenses/noncommercial/1.0.0
Required Notice: Copyright (c) 2026 HGhubX contributors (https://github.com/zxsos/HGhubX)
"""

import base64
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from urllib.parse import urlencode, urljoin, urlsplit

from requests import RequestException, Session

from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """以访客身份读取短剧目录、封面、分集和播放地址。"""

    display_name = "黄果视频源"

    SITE = "https://huangguoai.com"
    API_HOSTS = ("huangguoai.com", "huangguoai.ai")
    # 黄果主站与 HGhubX 已记录的图片 CDN 后缀，代理跳转也使用同一范围。
    COVER_HOSTS = API_HOSTS + ("drleao.cn",)
    CATEGORIES = {
        "all": "全部短剧",
        "ai-duanju": "短剧",
        "ai-manju": "漫剧",
        "original": "原创",
        "other": "其他短剧",
    }
    CHANNELS = ("ai-duanju", "ai-manju")
    PAGE_SIZE = 20
    CATEGORY_CACHE_COUNT = 4096
    CATEGORY_CACHE_SECONDS = 600
    CATEGORY_LOOKUP_SECONDS = 20
    OTHER_SEED_SECONDS = 30
    MAX_COVER_BYTES = 4 * 1024 * 1024
    COVER_CACHE_BYTES = 16 * 1024 * 1024
    COVER_CACHE_COUNT = 240
    PLAY_LINE = "黄果访客"
    COVER_KEY = b"f5d965df75336270"
    COVER_IV = b"97b60394abc2fbe1"

    def init(self, extend=""):
        """初始化请求头、分类缓存和封面缓存。"""
        self.media_headers = {
            "Referer": self.SITE + "/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
        }
        self.cover_cache = OrderedDict()
        self.cover_cache_size = 0
        # 分类缓存只保存归属和已发现的其他短剧摘要，不保留详情或播放地址。
        self.category_cache = OrderedDict()
        self.other_cards = OrderedDict()
        self.other_seed_until = 0
        self.other_source_page = 1
        self.channel_pages = {channel: {} for channel in self.CHANNELS}

    def _positive_number(self, value):
        """读取源站数字 ID、集号或页码，拒绝布尔值和非数字标识。"""
        raw = str(value)
        return int(raw) if re.fullmatch(r"[1-9][0-9]{0,18}", raw) else 0

    def _https_url(self, value, base, allowed_hosts=(), allow_subdomains=False):
        """补齐相对地址，保持上游签名参数原样；代理请求限定到已知域名。"""
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            address = urljoin(base, value.strip())
            parsed = urlsplit(address)
            host = parsed.hostname or ""
            if (parsed.scheme != "https" or not host or parsed.port not in (None, 443)
                    or parsed.username is not None or parsed.password is not None):
                return ""
        except ValueError:
            return ""
        if allowed_hosts and not any(host == domain or (allow_subdomains and host.endswith("." + domain))
                                     for domain in allowed_hosts):
            return ""
        return address

    def _edge(self, path, query=None, method="GET", missing_statuses=(), missing_message="", send=None):
        """调用 HGhubX 使用的访客网关，保留业务失败与空目录之间的区别。"""
        if query:
            path += "?" + urlencode(query)
        # 逻辑 POST 的播放接口接收空 JSON 对象，网关信封仍使用 HTTP POST。
        envelope = {
            "method": method,
            "path": path,
            "body": base64.b64encode(b"{}").decode("ascii") if method == "POST" else "",
            "content_type": "application/json" if method == "POST" else "",
        }
        target = self.SITE + "/api/edge/request"
        headers = {**self.media_headers, "Accept": "application/json"}
        post = send or self.post
        for _ in range(3):
            with post(target, headers=headers, json=envelope, timeout=(4, 8),
                      allow_redirects=False) as response:
                status = response.status_code
                if status in (301, 302, 303) and method == "GET":
                    target = self._https_url(response.headers.get("Location"), target, self.API_HOSTS)
                    if not target:
                        raise RuntimeError("黄果访客网关跳转地址不可用")
                    continue
                if status in missing_statuses:
                    return None
                if status in (401, 403):
                    raise RuntimeError("黄果访客无权访问当前内容")
                if not 200 <= status < 300:
                    raise RuntimeError(f"黄果访客网关返回 HTTP {status}")
                result = response.json()
                if (missing_message and isinstance(result, dict) and result.get("status") == 0
                        and result.get("msg") == missing_message):
                    return None
                if not isinstance(result, dict) or result.get("status") != 1:
                    raise RuntimeError("黄果访客网关未返回成功结果")
                data = result.get("data")
                if not isinstance(data, dict):
                    raise RuntimeError("黄果访客网关返回的数据格式无效")
                return data
        raise RuntimeError("黄果访客网关跳转次数过多")

    def _card(self, item, category):
        """保留原作品 ID 和卡片字段，封面交给当前媒体库的图片代理。"""
        if not isinstance(item, dict):
            raise RuntimeError("黄果短剧条目格式无效")
        item_id = self._positive_number(item.get("id"))
        title = item.get("title")
        if not item_id or not isinstance(title, str) or not title.strip():
            raise RuntimeError("黄果短剧缺少作品 ID 或标题")
        cover = self._https_url(item.get("cover"), self.SITE + "/", self.COVER_HOSTS, True)
        # 封面定位保存在媒体库，worker 重启后可按原作品 ID 刷新失效的 CDN 地址。
        proxy = self.getProxyUrl()
        cover_url = f"{proxy}&{urlencode({'id': item_id, 'cover': cover})}" if cover and proxy else ""
        return {
            "type_id": category,
            "type_name": self.CATEGORIES[category],
            "vod_id": str(item_id),
            "vod_name": title.strip(),
            "vod_pic": cover_url,
            "vod_content": item.get("description") or "",
        }

    def _declared_category(self, item):
        """优先识别原创，其余按源站详情的分类路径判断。"""
        if str(item.get("is_original")) == "1":
            return "original"
        breadcrumb = item.get("breadcrumb")
        if isinstance(breadcrumb, list):
            for entry in breadcrumb:
                link = entry.get("url") if isinstance(entry, dict) else None
                if isinstance(link, str):
                    category = urlsplit(link).path.strip("/")
                    if category in (*self.CHANNELS, "original"):
                        return category
        return None

    def _cached_category(self, item_id):
        """使用尚未过期的作品分类。"""
        cached = self.category_cache.get(item_id)
        if cached and cached[1] > monotonic():
            self.category_cache.move_to_end(item_id)
            return cached[0]
        return None

    def _remember_category(self, card):
        """按作品 ID 缓存归属，归属变化时同步更新其他短剧目录。"""
        item_id = int(card["vod_id"])
        category = card["type_id"]
        if category == "all":
            return
        self.category_cache[item_id] = (category, monotonic() + self.CATEGORY_CACHE_SECONDS)
        self.category_cache.move_to_end(item_id)
        if category == "other":
            self.other_cards[item_id] = card
        else:
            self.other_cards.pop(item_id, None)
        while len(self.category_cache) > self.CATEGORY_CACHE_COUNT:
            expired_id, _ = self.category_cache.popitem(last=False)
            self.other_cards.pop(expired_id, None)

    def _lookup_category(self, item_id, deadline, send):
        """只查当前页缺少归属的作品，临时请求失败留待后续刷新重试。"""
        if monotonic() >= deadline:
            return "all"
        try:
            detail = self._detail(item_id, send=send)
        except (RequestException, RuntimeError, ValueError):
            return "all"
        if detail is None:
            return "all"
        return self._declared_category(detail) or "other"

    def _lookup_categories(self, item_ids, deadline):
        """同一批访客详情复用连接，各线程独立维护会话。"""
        with Session() as session:
            return {item_id: self._lookup_category(item_id, deadline, session.post) for item_id in item_ids}

    def _classify_page(self, items, channel=None):
        """合并源频道和详情证据，按原页顺序返回摘要。"""
        categories = {}
        missing = {}
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError("黄果短剧条目格式无效")
            item_id = self._positive_number(item.get("id"))
            if not item_id:
                raise RuntimeError("黄果短剧缺少作品 ID")
            category = self._declared_category(item) or channel or self._cached_category(item_id)
            if category:
                categories[item_id] = category
            else:
                missing[item_id] = None
        unresolved = [item_id for item_id in missing if item_id not in categories]
        if unresolved:
            # 只补当前页，限制同时访问上游的请求数量。
            deadline = monotonic() + self.CATEGORY_LOOKUP_SECONDS
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self._lookup_categories, unresolved[::2], deadline)
                second = pool.submit(self._lookup_categories, unresolved[1::2], deadline)
                categories.update(first.result())
                categories.update(second.result())
        cards = [self._card(item, categories[self._positive_number(item.get("id"))]) for item in items]
        for card in cards:
            self._remember_category(card)
        return cards

    def _source_page(self, path, page, query):
        """读取上游目录原页及分页数据。"""
        data = self._edge(path, {**query, "page": page, "size": self.PAGE_SIZE})
        items = data.get("items")
        pagination = data.get("pagination")
        if not isinstance(items, list) or not isinstance(pagination, dict):
            raise RuntimeError("黄果未返回有效目录或分页")
        return items, pagination

    def _page(self, path, page, query, channel=None):
        """返回按原站顺序分页的全部、原创或搜索摘要。"""
        items, pagination = self._source_page(path, page, query)
        pages = self._positive_number(pagination.get("pages"))
        size = self._positive_number(pagination.get("size")) or self.PAGE_SIZE
        result = {"list": self._classify_page(items, channel), "page": page, "limit": size}
        if pages or not items:
            result["pagecount"] = pages or page
        total = pagination.get("total")
        if type(total) is int and total >= 0:
            result["total"] = total
        return result

    def _channel_page(self, channel, page):
        """跳过全部是原创的源页，保持插件分页连续。"""
        # 每个有效页记录对应的源页，后续请求从最近的已知位置接着读取。
        checkpoints = self.channel_pages[channel]
        if page == 1:
            checkpoints.clear()
        current = max((number for number in checkpoints if number <= page), default=1)
        source_page = checkpoints.get(current, 1)
        while True:
            items, pagination = self._source_page(f"/api/videos/category/{channel}", source_page, {})
            last_page = self._positive_number(pagination.get("pages"))
            cards = [card for card in self._classify_page(items, channel) if card["type_id"] == channel]
            has_more = bool(items) and (not last_page or source_page < last_page)
            if cards and current == page:
                checkpoints[page] = source_page
                if has_more:
                    checkpoints[page + 1] = source_page + 1
                return {"list": cards, "page": page, "pagecount": page + int(has_more)}
            if not has_more:
                return {"list": [], "page": page, "pagecount": page}
            source_page += 1
            if cards:
                current += 1

    def _other_page(self, page):
        """从已识别的目录分页，间隔一段时间补查一个源页。"""
        if page == 1 and monotonic() >= self.other_seed_until:
            source_page = self.other_source_page
            result = self._page("/api/videos", source_page, {})
            if result["list"] and source_page < result.get("pagecount", source_page + 1):
                self.other_source_page += 1
            else:
                self.other_source_page = 1
            self.other_seed_until = monotonic() + self.OTHER_SEED_SECONDS
        cards = list(self.other_cards.values())
        start = (page - 1) * self.PAGE_SIZE
        count = max(1, (len(cards) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        return {"list": cards[start:start + self.PAGE_SIZE], "page": page,
                "pagecount": count, "limit": self.PAGE_SIZE, "total": len(cards)}

    def homeContent(self, filter):
        """提供总目录和可独立浏览的分类。"""
        classes = [{"type_id": category, "type_name": name, "type_all": category == "all"}
                   for category, name in self.CATEGORIES.items()]
        return {"class": classes, "list": []}

    def categoryContent(self, tid, pg, filter, extend):
        """按所选分类读取目录摘要。"""
        page = self._positive_number(pg)
        category = str(tid)
        if category not in self.CATEGORIES or not page:
            raise ValueError("黄果分类或页码无效")
        if category in self.CHANNELS:
            return self._channel_page(category, page)
        if category == "original":
            return self._page("/api/videos", page, {"is_original": 1}, category)
        if category == "other":
            return self._other_page(page)
        return self._page("/api/videos", page, {})

    def searchContent(self, key, quick, pg="1"):
        """搜索源站视频目录，并为云助手提供各作品的分类。"""
        page = self._positive_number(pg)
        if not page:
            raise ValueError("黄果搜索页码无效")
        keyword = str(key or "").strip()
        if not keyword:
            return {"list": [], "page": page, "pagecount": 1, "total": 0}
        return self._page("/api/search", page, {"q": keyword, "type": "video"})

    def _detail(self, item_id, send=None):
        """按作品 ID 读取访客详情，确认返回的仍是同一部剧。"""
        data = self._edge(f"/api/videos/{item_id}", missing_statuses=(404,),
                          missing_message="视频不存在", send=send)
        if data is not None and self._positive_number(data.get("id")) != item_id:
            raise RuntimeError("黄果详情返回的作品 ID 不一致")
        return data

    def detailContent(self, ids):
        """按需读取选集，给云助手保留源站真实集号。"""
        item_id = self._positive_number(ids[0]) if ids else 0
        if not item_id:
            return {"list": []}
        data = self._detail(item_id)
        if data is None:
            return {"list": []}
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            raise RuntimeError("黄果未返回访客选集")
        # 宿主以「第N集」识别实际集号；取址标识只保留作品 ID 和集号。
        numbers = set()
        for episode in episodes:
            number = self._positive_number(episode.get("ep_num")) if isinstance(episode, dict) else 0
            if number:
                numbers.add(number)
        if not numbers:
            raise RuntimeError("黄果未返回可识别的访客分集")
        category = self._declared_category(data) or self._cached_category(item_id) or "other"
        card = self._card(data, category)
        self._remember_category(card)
        return {"list": [{
            **card,
            "vod_play_from": self.PLAY_LINE,
            "vod_play_url": "#".join(f"第{number}集${item_id}:{number}" for number in sorted(numbers)),
        }]}

    def _play_address(self, data):
        """取上游授权的主播放线路，优先使用现有主线路字段。"""
        streams = data.get("streams")
        main = streams.get("main") if isinstance(streams, dict) else None
        for candidate in (data.get("video_url"), main):
            address = self._https_url(candidate, self.SITE + "/")
            if address:
                return address
        return ""

    def playerContent(self, flag, id, vipFlags):
        """在播放时请求当前集的临时地址，供云助手现有媒体中转使用。"""
        item, _, episode = str(id).partition(":")
        item_id, number = self._positive_number(item), self._positive_number(episode)
        if flag != self.PLAY_LINE or not item_id or not number:
            raise ValueError("黄果分集播放标识无效")
        data = self._edge(f"/api/videos/{item_id}/episodes/{number}/playback", method="POST",
                          missing_statuses=(404, 405))
        address = self._play_address(data or {})
        if not address:
            data = self._edge(f"/api/videos/{item_id}/play", {"ep": number, "page_texts": 0})
            address = self._play_address(data)
        if not address:
            raise RuntimeError("黄果未返回访客可播地址")
        result = {"parse": 0, "url": address, "header": self.media_headers.copy()}
        path = urlsplit(address).path.lower()
        if path.endswith(".m3u8"):
            result["type"] = "hls"
        elif path.endswith(".mp4"):
            result["type"] = "mp4"
        return result

    def _image_type(self, content):
        """按封面实际字节判断格式。"""
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return ""

    def _read_cover(self, target):
        """只从源站图片域读取有界封面；过期状态交给调用方刷新地址。"""
        for _ in range(3):
            with self.fetch(target, headers=self.media_headers, stream=True, timeout=(3, 5),
                            allow_redirects=False) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    target = self._https_url(response.headers.get("Location"), target, self.COVER_HOSTS, True)
                    if not target:
                        raise RuntimeError("黄果封面跳转地址不可用")
                    continue
                if status in (401, 403, 404):
                    return None
                if status != 200:
                    raise RuntimeError(f"黄果封面返回 HTTP {status}")
                body = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    if len(body) + len(chunk) > self.MAX_COVER_BYTES:
                        raise RuntimeError("黄果封面超过大小限制")
                    body.extend(chunk)
                return bytes(body)
        raise RuntimeError("黄果封面跳转次数过多")

    def _decode_cover(self, content):
        """返回普通图片，或用源站封面格式解密后再识别 MIME。"""
        mime = self._image_type(content)
        if mime:
            return mime, content
        if not content or len(content) % 16:
            raise RuntimeError("黄果封面格式无效")
        from Crypto.Cipher import AES

        clear = AES.new(self.COVER_KEY, AES.MODE_CBC, self.COVER_IV).decrypt(content)
        padding = clear[-1]
        if 1 <= padding <= 16 and clear[-padding:] == bytes([padding]) * padding:
            clear = clear[:-padding]
        mime = self._image_type(clear)
        if not mime:
            raise RuntimeError("黄果封面解密后未识别为图片")
        return mime, clear

    def localProxy(self, param):
        """通过宿主图片入口返回封面，复用缓存并按需更新过期地址。"""
        item_id = self._positive_number(param.get("id"))
        cover = self._https_url(param.get("cover"), self.SITE + "/", self.COVER_HOSTS, True)
        if not item_id or not cover:
            return [404, "text/plain", b""]
        cache_key = (item_id, cover)
        cached = self.cover_cache.get(cache_key)
        if cached is not None:
            self.cover_cache.move_to_end(cache_key)
            return [200, *cached]
        content = self._read_cover(cover)
        if content is None:
            detail = self._detail(item_id)
            fresh = self._https_url((detail or {}).get("cover"), self.SITE + "/", self.COVER_HOSTS, True)
            if fresh and fresh != cover:
                content = self._read_cover(fresh)
        if content is None:
            return [404, "text/plain", b""]
        mime, image = self._decode_cover(content)
        self.cover_cache[cache_key] = (mime, image)
        self.cover_cache_size += len(image)
        while len(self.cover_cache) > self.COVER_CACHE_COUNT or self.cover_cache_size > self.COVER_CACHE_BYTES:
            _, (_, expired_image) = self.cover_cache.popitem(last=False)
            self.cover_cache_size -= len(expired_image)
        return [200, mime, image]
