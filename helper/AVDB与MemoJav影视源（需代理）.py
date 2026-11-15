# coding=utf-8
"""AVDB 目录与 MemoJav 播放源，使用云助手现有 Python Spider 接口。

目录、分类和影片资料来自 AVDB；MemoJav 按影片编号补充独立线路。
分集只保存稳定取址标识，播放时重新获取当前地址，不缓存签名直链。
"""

import base64
import json
import re
import sys
import time
from urllib.parse import unquote_plus, urljoin, urlsplit

from requests import Request, RequestException

from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """通过原生 HTTP 获取目录和两种来源的实际媒体地址。"""

    display_name = "AVDB与MemoJav影视源（需代理）"
    version = "1.0.0"

    AVDB = "https://avdbapi.com"
    MEMOJAV = "https://memojav.org"
    PAGE_SIZE = 20
    TIMEOUT = (4, 10)
    UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Version/4.0 Chrome/153.0.0.0 Mobile Safari/537.36")
    # 电影父分类让现有宿主按电影处理，子分类仍对应 AVDB 的真实分类 ID。
    MOVIES = "movies"
    # MP4 变体沿用 APK 的客户端后缀；地址是否有效仍由当前上游决定。
    MP4_VARIANTS = (("1080p", "=m37"), ("720p", "=m22"), ("360p", "=m18"))

    def init(self, extend=""):
        """初始化可选站点配置和轻量分类缓存，不请求影片或播放地址。"""
        config = extend if isinstance(extend, dict) else json.loads(extend) if extend else {}
        if not isinstance(config, dict):
            raise ValueError("插件配置必须是 JSON 对象")
        self.avdb = self._web_url(config.get("avdb") or self.AVDB).rstrip("/")
        self.memojav = self._web_url(config.get("memojav") or self.MEMOJAV).rstrip("/")
        self.user_agent = str(config.get("user_agent") or self.UA).strip()
        self.categories = []
        self.category_ids = {}
        self.categories_until = 0

    def getName(self):
        """返回宿主可静态识别的插件名称。"""
        return self.display_name

    def getDependence(self):
        """仅使用云助手 Python 宿主已有的 requests 和标准库。"""
        return ["requests"]

    def _event(self, event, **values):
        """记录中文诊断字段，不输出签名地址、Cookie 或影片标题。"""
        record = {"日志关键字": "codex-avdb-memojav", "事件": event, **values}
        sys.stderr.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _web_url(self, value):
        """读取明确的 HTTP 地址，不修改上游媒体路径及签名参数。"""
        if not isinstance(value, str):
            raise ValueError("源站未返回有效地址")
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("源站未返回有效 HTTP 地址")
        return value.strip()

    def _number(self, value):
        """读取正整数页码和 AVDB 影片 ID。"""
        raw = str(value)
        if not re.fullmatch(r"[1-9][0-9]*", raw):
            raise ValueError("页码或影片 ID 无效")
        return int(raw)

    def _avdb_data(self, **params):
        """读取 AVDB 的明确 JSON 契约，网络或结构错误不伪装成空目录。"""
        with self.fetch(self.avdb + "/api.php/provide/vod", params=params,
                        headers={"User-Agent": self.user_agent}, timeout=self.TIMEOUT) as response:
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or data.get("code") != 1 or not isinstance(data.get("list"), list):
            raise RuntimeError("AVDB 未返回有效影片目录")
        return data

    def _load_categories(self):
        """短时缓存上游真实分类，不硬编码备用分类或写入持久化数据。"""
        if self.categories and self.categories_until > time.monotonic():
            return
        data = self._avdb_data(ac="list", pg=1, pagesize=1)
        categories = data.get("class")
        if not isinstance(categories, list) or not categories:
            raise RuntimeError("AVDB 未返回分类目录")
        selected = []
        for item in categories:
            category_id = str(self._number(item["type_id"]))
            name = str(item.get("type_name") or "").strip()
            if not name:
                raise RuntimeError("AVDB 分类缺少名称")
            selected.append({"type_id": category_id, "type_name": name, "type_pid": self.MOVIES})
        self.categories = selected
        self.category_ids = {item["type_name"]: item["type_id"] for item in selected}
        self.categories_until = time.monotonic() + 600

    def _joined_text(self, value):
        """转换上游明确的人员、地区或标签列表，不猜测缺失资料。"""
        values = value if isinstance(value, list) else [value]
        return ",".join(self.cleanText(item) for item in values if isinstance(item, str) and item.strip())

    def _card(self, item):
        """把 AVDB 条目映射为真实分类下的摘要，不提前访问播放页面。"""
        item_id = str(self._number(item.get("id")))
        title = self.cleanText(item.get("name"))
        category_name = str(item.get("type_name") or "").strip()
        category_id = self.category_ids.get(category_name)
        if not title or not category_id:
            raise RuntimeError("AVDB 影片缺少名称或对应分类")
        poster = item.get("poster_url") or item.get("thumb_url") or ""
        return {
            "vod_id": item_id,
            "vod_name": title,
            "vod_pic": urljoin(self.avdb + "/", poster) if poster else "",
            "type_id": category_id,
            "type_id_1": self.MOVIES,
            "type_name": category_name,
            "vod_year": item.get("year") or "",
            "vod_area": self._joined_text(item.get("country")),
            "vod_actor": self._joined_text(item.get("actor")),
            "vod_director": self._joined_text(item.get("director")),
            "vod_class": self._joined_text(item.get("category")),
            "vod_tag": self._joined_text(item.get("tag")),
            "vod_remarks": self.cleanText(item.get("quality") or ""),
            "vod_content": self.cleanText(item.get("description") or ""),
        }

    def _page(self, data, page):
        """返回真实分页，摘要只取当前源页，不逐部补详情。"""
        self._load_categories()
        cards = [self._card(item) for item in data["list"]]
        count = data.get("pagecount")
        total = data.get("total")
        size = data.get("limit")
        return self.pageResult(cards, page,
                               int(count) if str(count).isdigit() else None,
                               int(size) if str(size).isdigit() and int(size) > 0 else self.PAGE_SIZE,
                               int(total) if str(total).isdigit() else None)

    def homeContent(self, filter):
        """返回电影父目录与 AVDB 真实子分类，不在建库阶段解析播放。"""
        self._load_categories()
        return {"class": [{"type_id": self.MOVIES, "type_name": "电影", "type_all": True},
                          *[dict(item) for item in self.categories]], "list": []}

    def homeVideoContent(self):
        """按需读取 AVDB 最新一页影片摘要。"""
        return self._page(self._avdb_data(ac="detail", pg=1, pagesize=self.PAGE_SIZE), 1)

    def categoryContent(self, tid, pg, filter, extend):
        """把真实分类和页码传给 AVDB，电影父目录对应全部影片。"""
        page = self._number(pg)
        self._load_categories()
        category = str(tid)
        if category != self.MOVIES and category not in self.category_ids.values():
            raise ValueError("AVDB 分类不存在")
        params = {"ac": "detail", "pg": page, "pagesize": self.PAGE_SIZE}
        if category != self.MOVIES:
            params["t"] = category
        return self._page(self._avdb_data(**params), page)

    def _code_key(self, value):
        """仅用于按 APK 规则精确比较编号，不改写请求 URL 或影片身份。"""
        return "".join(char for char in str(value).upper() if char.isalnum())

    def searchContent(self, key, quick, pg="1"):
        """编号优先精确匹配，未命中时使用 AVDB 原生关键字分页搜索。"""
        page = self._number(pg)
        keyword = str(key or "").strip()
        if not keyword:
            return self.pageResult([], page, page, self.PAGE_SIZE, 0)
        # 编号查询沿用 APK 的 trim、下划线替换和大写处理，中文关键词直接走 wd。
        if re.fullmatch(r"[A-Za-z0-9_-]+", keyword) and any(char.isdigit() for char in keyword):
            code = keyword.replace("_", "-").upper()
            data = self._avdb_data(ac="detail", code=code, pagesize=100)
            matched = [item for item in data["list"]
                       if self._code_key(item.get("movie_code") or item.get("slug") or "") == self._code_key(code)]
            if matched:
                unique = {str(item["id"]): item for item in matched}
                items = list(unique.values())
                return self._page({"list": items if page == 1 else [], "pagecount": 1,
                                   "limit": 100, "total": len(items)}, page)
        return self._page(self._avdb_data(ac="detail", wd=keyword, pg=page, pagesize=self.PAGE_SIZE), page)

    def _detail(self, item_id):
        """按 ID 查当前详情，避免使用其他影片或目录页的第一条记录。"""
        data = self._avdb_data(ac="detail", ids=self._number(item_id), pagesize=100)
        return next((item for item in data["list"] if str(item.get("id")) == str(item_id)), None)

    def _avdb_lines(self, item):
        """枚举详情中真实发布的嵌入页，保留每条 server_data 的稳定键。"""
        episodes = item.get("episodes")
        servers = episodes.get("server_data") if isinstance(episodes, dict) else None
        if not isinstance(servers, dict):
            return []
        lines = []
        for key, server in servers.items():
            address = server.get("link_embed") if isinstance(server, dict) else None
            if isinstance(address, str) and address.startswith(("https://", "http://")):
                lines.append((str(key), address))
        return lines

    def _play_id(self, source, **values):
        """保存稳定取址参数，Base64 只是避开 Spider 的 # 和 $ 分隔符。"""
        raw = json.dumps({"source": source, **values}, ensure_ascii=False, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    def _read_play_id(self, value):
        """还原当前插件发布的取址参数，不接受缓存的媒体直链充当身份。"""
        raw = str(value)
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        if not isinstance(data, dict) or data.get("source") not in ("avdb", "memojav"):
            raise ValueError("播放标识无效")
        return data

    def _memo_code(self, item):
        """只使用 AVDB 明确给出的影片编号，不从标题或 URL 猜测。"""
        code = str(item.get("movie_code") or "").strip().upper()
        return code if re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code) else ""

    def _memo_text(self, path, params=None, missing=False):
        """按 MemoJav 的原生请求头读取响应，404 和网络失败分别处理。"""
        headers = {"User-Agent": self.user_agent, "Accept": "application/json, text/plain, */*"}
        with self.fetch(self.memojav + path, params=params, headers=headers, timeout=self.TIMEOUT) as response:
            if missing and response.status_code == 404:
                return None
            response.raise_for_status()
            text = response.text
        if re.search(r"challenge-platform|cf-turnstile|<title>Just a moment", text, re.IGNORECASE):
            raise RuntimeError("MemoJav 当前需要网页验证")
        return text

    def _memo_playback(self, code):
        """实时生成 APK 的 sig/sts，读取当前 HLS 或 MP4，不缓存签名 URL。"""
        encoded = base64.b64encode(str(int(time.time() * 1000)).encode("utf-8")).decode("ascii")
        signature = encoded[max(len(encoded) - 12, 0):][:10]
        stamp = 1 + sum(ord(char) * index * 1743 for index, char in enumerate(signature))
        text = self._memo_text("/hls/get_video_info.php", {"id": code, "sig": signature, "sts": stamp})
        data = json.loads(text.strip().removeprefix("for (;;);").strip())
        if not isinstance(data, dict):
            raise RuntimeError("MemoJav 未返回有效播放信息")
        media_type = str(data.get("type") or "").lower()
        if data.get("success") is not True or media_type in ("deleted", "encoding"):
            return None
        if media_type not in ("hls", "mp4"):
            raise RuntimeError("MemoJav 返回了未知媒体类型")
        address = self._web_url(unquote_plus(str(data.get("url") or "")))
        if not address.startswith("https://") or (media_type == "hls" and ".m3u8" not in address.lower()):
            raise RuntimeError("MemoJav 未返回有效媒体地址")
        return {"type": media_type, "url": address}

    def _memo_options(self, code):
        """详情阶段只确认资源存在和清晰度，临时地址不进入详情结果。"""
        embed = self._memo_text("/embed/" + code, missing=True)
        if not embed or not embed.strip():
            return []
        playback = self._memo_playback(code)
        if playback is None:
            return []
        if playback["type"] == "hls":
            return [("MemoJav", "primary")]
        return [("MemoJav " + quality, quality) for quality, _ in self.MP4_VARIANTS]

    def detailContent(self, ids):
        """读取单部资料，展示真实 AVDB 线路与已确认存在的 MemoJav 资源。"""
        if not ids:
            return {"list": []}
        item_id = str(self._number(ids[0]))
        self._load_categories()
        item = self._detail(item_id)
        if item is None:
            return {"list": []}
        names, groups = [], []
        avdb_lines = self._avdb_lines(item)
        for index, (server_key, _) in enumerate(avdb_lines, 1):
            names.append("AVDB" if len(avdb_lines) == 1 else "AVDB 线路" + str(index))
            groups.append("正片$" + self._play_id("avdb", item=item_id, server=server_key))
        code = self._memo_code(item)
        if code:
            try:
                options = self._memo_options(code)
            except (RequestException, RuntimeError, ValueError) as error:
                # MemoJav 是可选补充源，失败不使已经有效的 AVDB 线路一起失效。
                self._event("MemoJav详情线路不可用", 影片编号=code, 错误类型=type(error).__name__)
                options = []
            for line_name, variant in options:
                names.append(line_name)
                groups.append("正片$" + self._play_id("memojav", code=code, variant=variant))
        if not groups:
            raise RuntimeError("当前影片没有可用播放线路")
        return {"list": [{**self._card(item), "vod_play_from": "$$$".join(names),
                           "vod_play_url": "$$$".join(groups)}]}

    def _with_cookies(self, url, headers):
        """只附加当前媒体 URL 实际适用的会话 Cookie，由宿主媒体中转继续使用。"""
        selected = dict(headers)
        prepared = self._session().prepare_request(Request("GET", url, headers=selected))
        if prepared.headers.get("Cookie"):
            selected["Cookie"] = prepared.headers["Cookie"]
        return selected

    def _resolve_avdb(self, data):
        """播放时重查指定线路嵌入页，生成与 APK 相同的 HLS 请求头。"""
        item = self._detail(str(self._number(data.get("item"))))
        if item is None:
            raise RuntimeError("AVDB 影片已不存在")
        embed = next((url for key, url in self._avdb_lines(item) if key == data.get("server")), None)
        if embed is None:
            raise RuntimeError("AVDB 所选线路已不存在，请刷新详情")
        with self.fetch(embed, headers={"User-Agent": self.user_agent, "Referer": self.avdb + "/"},
                        timeout=self.TIMEOUT) as response:
            response.raise_for_status()
            final_url = response.url
            match = re.search(r"window\.PLAYER_CONFIG\s*=\s*(\{.*?\})\s*;", response.text, re.DOTALL)
        if not match:
            raise RuntimeError("AVDB 播放页面未返回 PLAYER_CONFIG")
        address = self._web_url(json.loads(match.group(1)).get("m3u8"))
        parsed = urlsplit(final_url)
        headers = {"User-Agent": self.user_agent, "Referer": final_url.rstrip("/") + "/",
                   "Origin": parsed.scheme + "://" + parsed.hostname, "Sec-Fetch-Mode": "cors",
                   "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "cross-site", "Accept": "*/*"}
        # 仅检查清单前缀，不把可能很大的 HLS 清单或影片下载到 Python 内存。
        with self.fetch(address, headers=headers, stream=True, timeout=self.TIMEOUT) as response:
            response.raise_for_status()
            prefix = next(response.iter_content(4096), b"").decode("utf-8-sig", "replace").lstrip()
            if not prefix.startswith("#EXTM3U"):
                raise RuntimeError("AVDB 返回的地址不是有效 HLS 清单")
        return {"parse": 0, "url": address, "type": "hls", "header": self._with_cookies(address, headers)}

    def _resolve_memo(self, data):
        """重新请求 MemoJav，严格保留用户选中的 HLS 或 MP4 清晰度。"""
        code = str(data.get("code") or "")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
            raise ValueError("MemoJav 影片编号无效")
        playback = self._memo_playback(code)
        if playback is None:
            raise RuntimeError("MemoJav 资源已删除、转码中或暂不可用")
        variant = data.get("variant")
        if playback["type"] == "hls":
            if variant != "primary":
                raise RuntimeError("MemoJav 清晰度已变化，请刷新详情")
            address = playback["url"]
        else:
            suffix = dict(self.MP4_VARIANTS).get(variant)
            if suffix is None:
                raise RuntimeError("MemoJav 清晰度已变化，请刷新详情")
            address = playback["url"] + suffix
        headers = self._with_cookies(address, {"User-Agent": self.user_agent})
        return {"parse": 0, "url": address, "type": playback["type"], "header": headers}

    def playerContent(self, flag, id, vipFlags):
        """只解析用户选择的线路，失败时不自动换源或返回网页充当媒体。"""
        data = self._read_play_id(id)
        try:
            if data["source"] == "avdb":
                return self._resolve_avdb(data)
            return self._resolve_memo(data)
        except (RequestException, RuntimeError, ValueError) as error:
            self._event("播放取址失败", 播放来源=data["source"], 错误类型=type(error).__name__)
            raise
