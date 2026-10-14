# -*- coding: utf-8 -*-
"""白嫖者联盟独立 Python Spider；列表只返回摘要，分集及取址按需请求。"""

import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

from base.spider import Spider as BaseSpider


SITE_URL = "https://bpz.app"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# 关键变量：原脚本公开 Web 客户端的签名参数，不是用户登录凭据。
SIGN_SECRET = "f39d73aa7a6426203cdee1ef17b31d3b7ea8c23f4c59c62a3a8aa0f39ee5e79d"
CLIENT_NAME = "movie-search-frontend"
CLIENT_VERSION = "1.0.0"
PROTOCOL_VERSION = "2026-07-05.library-v2.playback-v1"
BUILD_VERSION = "aimovie-v2026.08.15.5-150e53a6eed3"
PAGE_SIZE = 50
DETAIL_CACHE_SECONDS = 300
LINE_CACHE_SECONDS = 60
# 关键变量：使用内容类型作为固定分类；热榜只是同一目录的排序视图。
CATEGORIES = (
    ("k_movie", "电影"),
    ("k_series", "剧集"),
    ("k_short_drama", "短剧"),
    ("k_anime", "动漫"),
    ("k_variety", "综艺"),
    ("k_documentary", "纪录片"),
    ("k_sports", "体育"),
)
CATEGORY_NAMES = dict(CATEGORIES)


class Spider(BaseSpider):
    """源实例复用 HTTP 会话；签名 nonce 和临时票据只保留在运行进程。"""

    display_name = "白嫖者联盟"

    def init(self, extend=""):
        """初始化缓存但不联网，宿主重复初始化不会丢失现有会话。"""
        if not hasattr(self, "_detail_cache"):
            self._detail_cache = {}
            self._line_cache = {}
        return ""

    def getName(self):
        """返回固定名称，供插件导入时静态读取。"""
        return "白嫖者联盟"

    def _signed_headers(self, path):
        """每次请求独立签名，重试不会复用已被原站消费的 nonce。"""
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        message = "GET\n%s\n%s\n%s" % (path, timestamp, nonce)
        signature = hmac.new(
            SIGN_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": SITE_URL + "/",
            "x-ai-movie-timestamp": timestamp,
            "x-ai-movie-nonce": nonce,
            "x-ai-movie-signature": signature,
            "x-ai-movie-client-name": CLIENT_NAME,
            "x-ai-movie-client-version": CLIENT_VERSION,
            "x-ai-movie-protocol-version": PROTOCOL_VERSION,
            "x-ai-movie-build-version": BUILD_VERSION,
        }

    def _api(self, path):
        """使用源站同域签名接口；网络失败明确报告，不返回伪空目录。"""
        for attempt in range(2):
            try:
                response = self.fetch(
                    SITE_URL + path, headers=self._signed_headers(path), timeout=15
                )
            except Exception:
                if attempt == 0:
                    continue
                raise RuntimeError("白嫖者联盟接口网络请求失败") from None
            if response.status_code >= 500 and attempt == 0:
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    "白嫖者联盟接口请求失败，HTTP %s" % response.status_code
                )
            try:
                data = response.json()
            except ValueError:
                raise RuntimeError("白嫖者联盟接口没有返回有效 JSON") from None
            if not isinstance(data, dict):
                raise RuntimeError("白嫖者联盟接口响应结构无效")
            if data.get("error"):
                raise RuntimeError("白嫖者联盟接口返回错误")
            return data
        raise RuntimeError("白嫖者联盟接口请求失败")

    def _page_number(self, value):
        """只接受正整数页码，避免重复第一页冒充分页。"""
        number = str(value)
        if not number.isdigit() or int(number) < 1:
            raise ValueError("白嫖者联盟页码必须是正整数")
        return int(number)

    def _category_id(self, record):
        """使用原站 content_kind，不按影片标题猜分类或额外补详情。"""
        category_id = "k_" + str(record.get("content_kind") or "")
        if category_id not in CATEGORY_NAMES:
            raise RuntimeError("白嫖者联盟影片缺少支持的内容分类")
        return category_id

    def _poster(self, value):
        """沿用原源海报参数，只替换已经明确的 WebP 格式请求。"""
        return str(value or "").replace("fmt=webp", "fmt=jpeg")

    def _card(self, record):
        """卡片仅含已有摘要字段，不保存源站附带的票据或播放分组。"""
        movie_id = str(record.get("id") or record.get("variant_id") or "")
        title = str(record.get("title") or "").strip()
        if not movie_id or not title:
            raise RuntimeError("白嫖者联盟影片缺少原始 ID 或名称")
        category_id = self._category_id(record)
        return {
            "vod_id": movie_id,
            "type_id": category_id,
            "type_name": CATEGORY_NAMES[category_id],
            "vod_name": title,
            "vod_pic": self._poster(record.get("poster_url") or record.get("backdrop_url")),
            "vod_remarks": str(record.get("remarks") or ""),
            "vod_year": str(record.get("year") or ""),
            "vod_area": str(record.get("area") or ""),
            "vod_class": self._join(record.get("genres")),
        }

    def _join(self, value):
        """源站的字符串数组以逗号连接，已经是文本时保持原文。"""
        if isinstance(value, list):
            return ",".join(str(item) for item in value if item)
        return str(value or "")

    def _page_result(self, data, page):
        """采用原站返回的总数和分页标识，不虚构固定页数。"""
        records = data.get("cards")
        pagination = data.get("pagination")
        if not isinstance(records, list) or not isinstance(pagination, dict):
            raise RuntimeError("白嫖者联盟目录缺少卡片或分页信息")
        total = pagination.get("total")
        limit = pagination.get("limit")
        if not isinstance(total, int) or total < 0:
            raise RuntimeError("白嫖者联盟目录总数无效")
        if not isinstance(limit, int) or limit < 1:
            raise RuntimeError("白嫖者联盟目录分页大小无效")
        cards = []
        seen = set()  # 关键变量：同页重复的原始影片 ID 只展示一次。
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError("白嫖者联盟目录卡片结构无效")
            card = self._card(record)
            if card["vod_id"] in seen:
                continue
            seen.add(card["vod_id"])
            cards.append(card)
        page_count = max(1, (total + limit - 1) // limit)
        if pagination.get("has_more") is False:
            page_count = min(page_count, page)
        return {"list": cards, "page": page, "pagecount": page_count,
                "limit": limit, "total": total}

    def homeContent(self, filter=False):
        """首页只声明七个真实内容分类，不抓取分类列表和影片详情。"""
        return {"class": [
            {"type_id": category_id, "type_name": name}
            for category_id, name in CATEGORIES
        ]}

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        """每次只请求当前分类的一页摘要。"""
        category_id = str(tid)
        if category_id not in CATEGORY_NAMES:
            raise ValueError("白嫖者联盟分类不存在")
        page = self._page_number(pg)
        parameters = {"kind": category_id[2:], "page": page, "limit": PAGE_SIZE}
        if isinstance(extend, dict):
            for key in ("area", "genre", "year"):
                if extend.get(key) and extend[key] != "全部":
                    parameters[key] = str(extend[key])
        return self._page_result(
            self._api("/v1/browse/catalog?" + urlencode(parameters)), page
        )

    def searchContent(self, key, quick=False, pg="1"):
        """关键词搜索支持原站分页，每条卡片带真实分类供媒体库筛选。"""
        page = self._page_number(pg)
        keyword = str(key or "").strip()
        if not keyword:
            return {"list": [], "page": page, "pagecount": 1,
                    "limit": PAGE_SIZE, "total": 0}
        parameters = {"q": keyword, "page": page, "limit": PAGE_SIZE}
        return self._page_result(
            self._api("/v1/browse/catalog?" + urlencode(parameters)), page
        )

    def _details(self, movie_id):
        """只在详情或播放入口加载分集；短暂缓存票据，重启后可重新获取。"""
        self.init()
        cached = self._detail_cache.get(movie_id)
        if cached and time.time() - cached[0] < DETAIL_CACHE_SECONDS:
            return cached[1]
        path = "/v1/catalog/" + quote(movie_id, safe="")
        data = self._api(path)
        if movie_id not in {str(data.get("id") or ""), str(data.get("variant_id") or "")}:
            raise RuntimeError("白嫖者联盟返回了不匹配的影片详情")
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            raise RuntimeError("白嫖者联盟详情缺少分集列表")
        episodes = list(episodes)
        pagination = data.get("episode_pagination") or {}
        offset = pagination.get("offset", 0) + pagination.get("returned_count", len(episodes))
        seen = {str(item.get("id") or "") for item in episodes if isinstance(item, dict)}
        for _ in range(20):
            if not pagination.get("has_more"):
                break
            more = self._api(path + "/episodes?" + urlencode({"offset": offset, "limit": 100}))
            new_episodes = more.get("episodes")
            if not isinstance(new_episodes, list) or not new_episodes:
                raise RuntimeError("白嫖者联盟后续分集获取失败")
            for episode in new_episodes:
                if not isinstance(episode, dict) or not episode.get("id"):
                    raise RuntimeError("白嫖者联盟分集缺少原始 ID")
                if str(episode["id"]) not in seen:
                    seen.add(str(episode["id"]))
                    episodes.append(episode)
            pagination = more.get("episode_pagination") or {}
            next_offset = pagination.get("offset", offset) + pagination.get("returned_count", len(new_episodes))
            if pagination.get("has_more") and next_offset <= offset:
                raise RuntimeError("白嫖者联盟分集分页未前进")
            offset = next_offset
        if pagination.get("has_more"):
            raise RuntimeError("白嫖者联盟分集超过当前单次加载范围")
        data = dict(data, episodes=episodes)
        if len(self._detail_cache) >= 32:
            self._detail_cache.clear()
        self._detail_cache[movie_id] = (time.time(), data)
        return data

    def _line_identity(self, line):
        """线路身份包含真实 provider、播放类型和源影片 ID，禁止串线。"""
        provider = str(line.get("provider_id") or "")
        if not provider:
            raise RuntimeError("白嫖者联盟线路缺少 provider ID")
        return "@@".join(quote(str(value or ""), safe="") for value in (
            provider, line.get("play_from"), line.get("source_vod_id")
        ))

    def _lines(self, token):
        """源站同域提供线路解析接口，避免把浏览器播放器页交给客户端。"""
        self.init()
        cached = self._line_cache.get(token)
        if cached and time.time() - cached[0] < LINE_CACHE_SECONDS:
            return cached[1]
        data = self._api("/v1/playback/resolve/" + quote(token, safe=""))
        lines = data.get("line_options")
        if not isinstance(lines, list):
            raise RuntimeError("白嫖者联盟取址响应缺少线路列表")
        if not lines:
            raise RuntimeError("白嫖者联盟影片暂无可用线路")
        # 关键变量：顶层媒体请求头与线路请求头都保留，线路明确声明的字段优先。
        lines = [dict(line, headers=self._media_headers(
            data.get("headers"), data.get("header"),
            line.get("headers"), line.get("header")
        )) for line in lines]
        # 关键变量：先展示原站已经解析的线路，网页解析线路仍保留并明确报错。
        lines = sorted(lines, key=lambda line: 0 if self._is_media_line(line) else 1)
        if len(self._line_cache) >= 64:
            self._line_cache.clear()
        self._line_cache[token] = (time.time(), lines)
        return lines

    def _is_media_line(self, line):
        """只接纳原站明确声明的媒体类型或已经完成的 HTTP 媒体解析。"""
        if not isinstance(line, dict):
            return False
        parsed = urlsplit(str(line.get("url") or ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if str(line.get("resolve_mode") or "").lower() in ("parse", "web", "iframe"):
            return False
        return line.get("resolved") is True or str(line.get("url_kind") or "").lower() in (
            "m3u8", "mp4", "flv", "mkv", "ts", "m4v", "webm"
        )

    def _episode_name(self, episode, movie):
        """保留源站真实集号和特殊片段，不以数组位置拼集数。"""
        title = re.sub(r"[#$\r\n]", " ", str(episode.get("title") or "")).strip()
        track = str(episode.get("episode_track") or "main")
        if track != "main":
            if not title:
                raise RuntimeError("白嫖者联盟特殊片段缺少名称")
            # 关键变量：宿主在任意位置匹配 SxEy；分开标记避免花絮覆盖普通集身份。
            title = re.sub(r"S(\d+)\s*E(\d+)", r"S\1·E\2", title, flags=re.I)
            return "特别片段 · " + title
        if movie:
            return title if title and not re.fullmatch(r"(?:第\s*)?\d+\s*(?:集|话|期)?|正片|HD|高清", title, re.I) else "正片"
        number = episode.get("number")
        if isinstance(number, int) and number > 0:
            season = episode.get("season_number")
            if isinstance(season, int) and season > 0:
                return "S%sE%s" % (season, number)
            return "第%s集" % number
        if title:
            return title
        raise RuntimeError("白嫖者联盟分集缺少真实集号和名称")

    def _detail_item(self, movie_id):
        """完整详情中仅保存稳定的影片、分集及线路身份，不保存临时 URL。"""
        data = self._details(movie_id)
        vod = self._card(data)
        # 关键变量：源站可能同时返回作品 ID 与所选版本 ID，详情保持调用方的原版本身份。
        vod["vod_id"] = movie_id
        vod.update({
            "vod_lang": str(data.get("language") or ""),
            "vod_actor": self._join(data.get("actors")),
            "vod_director": self._join(data.get("directors")),
            "vod_content": str(data.get("description") or ""),
        })
        episodes = data["episodes"]
        if not episodes:
            raise RuntimeError("白嫖者联盟影片暂无可播放分集")
        first_token = str(episodes[0].get("token") or "")
        if not first_token:
            raise RuntimeError("白嫖者联盟分集缺少取址票据")
        lines = self._lines(first_token)
        froms, groups = [], []
        line_ids = set()
        line_names = set()  # 关键变量：宿主以线路名生成版本身份，重复名称不能静默覆盖。
        movie = vod["type_id"] == "k_movie"
        for line in lines:
            identity = self._line_identity(line)
            if identity in line_ids:
                raise RuntimeError("白嫖者联盟返回重复线路身份")
            line_ids.add(identity)
            label = str(line.get("display_label") or line.get("label") or line.get("provider_name") or line["provider_id"])
            label = re.sub(r"[#$\r\n]", " ", label).strip()
            # 线路完整身份摘要保持同源版本稳定，也区分同 provider 的不同源影片。
            line_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
            line_name = "白嫖者联盟 · %s (%s/%s · %s)" % (
                label, line["provider_id"], line.get("play_from") or "默认", line_digest
            )
            if line_name in line_names:
                raise RuntimeError("白嫖者联盟返回重复线路名称")
            line_names.add(line_name)
            froms.append(line_name)
            entries, names = [], set()
            for episode in episodes:
                episode_id = str(episode.get("id") or "")
                if not episode_id or not episode.get("token"):
                    raise RuntimeError("白嫖者联盟分集缺少原始 ID 或取址票据")
                name = self._episode_name(episode, movie)
                if name in names:
                    raise RuntimeError("白嫖者联盟同一线路分集名称重复")
                names.add(name)
                entries.append("%s$%s@@%s@@%s" % (
                    name, quote(movie_id, safe=""), quote(episode_id, safe=""), identity
                ))
            groups.append("#".join(entries))
        vod.update({"vod_play_from": "$$$".join(froms), "vod_play_url": "$$$".join(groups)})
        return vod

    def detailContent(self, ids):
        """仅对用户实际访问的影片补齐完整详情和线路。"""
        return {"list": [self._detail_item(str(movie_id)) for movie_id in ids]}

    def _media_headers(self, *values):
        """按顶层到线路的优先级保留媒体请求头，不猜测原站未声明的 Referer。"""
        headers = {"User-Agent": USER_AGENT}
        for raw in values:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise RuntimeError("白嫖者联盟播放请求头格式无效") from None
            if raw is not None and not isinstance(raw, dict):
                raise RuntimeError("白嫖者联盟播放请求头格式无效")
            if isinstance(raw, dict):
                headers.update(raw)
        return headers

    def _media_result(self, line, extra_headers=None):
        """保持原站媒体请求头，格式不明确时交给云助手检测媒体正文。"""
        if not self._is_media_line(line):
            raise RuntimeError("白嫖者联盟此线路未返回直连媒体，请选择其他线路")
        headers = self._media_headers(extra_headers, line.get("headers"), line.get("header"))
        result = {"parse": 0, "url": str(line["url"]), "header": headers}
        kind = str(line.get("url_kind") or "").lower()
        if kind in ("m3u8", "mp4", "flv", "mkv", "ts", "m4v", "webm"):
            result["type"] = kind
        return result

    def playerContent(self, flag, id, vipFlags=None):
        """根据原影片、原分集和原线路取址，失败时绝不切换另一条线路。"""
        from urllib.parse import unquote
        parts = str(id or "").split("@@")
        if len(parts) != 5 or not parts[0] or not parts[1] or not parts[2]:
            raise ValueError("白嫖者联盟播放标识无效")
        movie_id, episode_id = unquote(parts[0]), unquote(parts[1])
        identity = "@@".join(parts[2:])
        episode = next((item for item in self._details(movie_id)["episodes"]
                        if str(item.get("id") or "") == episode_id), None)
        if episode is None or not episode.get("token"):
            raise RuntimeError("白嫖者联盟原分集不存在或取址票据缺失")
        token = str(episode["token"])
        target = next((line for line in self._lines(token)
                       if self._line_identity(line) == identity), None)
        if target is None:
            raise RuntimeError("白嫖者联盟所选线路在此集不可用")
        if self._is_media_line(target):
            return self._media_result(target)
        playback_source_id = str(target.get("playback_source_id") or "")
        if not playback_source_id:
            raise RuntimeError("白嫖者联盟所选线路缺少换票标识")
        data = self._api("/v1/playback/resolve/%s?%s" % (
            quote(token, safe=""), urlencode({"ps": playback_source_id})
        ))
        resolved = next((line for line in data.get("line_options", [])
                         if self._line_identity(line) == identity), None)
        if resolved is None:
            raise RuntimeError("白嫖者联盟换票后没有返回所选线路")
        # 同源换票接口返回的仍为网页解析票据时明确报错，不把网页当视频。
        return self._media_result(resolved, self._media_headers(data.get("headers"), data.get("header")))
