# -*- coding: utf-8 -*-
"""青麦影视独立 Python Spider；目录、搜索、分集与取流均使用源站接口。"""

import base64
import math
import re
import uuid
from urllib.parse import urlsplit

from base.spider import Spider as BaseSpider


SITE_URL = "https://www.qingmaisp.com"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
PAGE_SIZE = 30
# 关键变量：使用源站原始分类 ID，保证首次搜索也能直接归入正确媒体库。
CATEGORIES = (
    ("M16", "电影"),
    ("M15", "电视剧"),
    ("M17", "动漫"),
    ("M18", "综艺"),
    ("M416", "纪录片"),
)
CATEGORY_NAMES = dict(CATEGORIES)


class Spider(BaseSpider):
    """每个源实例保存独立匿名设备会话，不需要用户账号或外部配置。"""

    display_name = "青麦影视"

    def init(self, extend=""):
        """幂等创建源站要求的 16 字符设备标识，不在初始化阶段联网。"""
        if not hasattr(self, "_device_id"):
            # 关键变量：原站只接受此长度的设备值；设备会话不会进入影片或播放身份。
            self._device_id = base64.b64encode(
                uuid.uuid4().hex[:16].encode("ascii")
            ).decode("ascii")
            self._token = ""
        return ""

    def getName(self):
        """返回稳定的插件显示名称，供云助手上传时静态识别。"""
        return "青麦影视"

    def _headers(self):
        """构造源站 Web 接口请求头，设备凭据只发往固定源站。"""
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json;charset=UTF-8",
            "client": "pc",
            "devicetype": "web",
            "useclient": "pc",
            "Referer": SITE_URL + "/",
        }

    def _read_response(self, response):
        """明确区分 HTTP、JSON 与接口错误，不把请求失败伪装成空目录。"""
        if response.status_code != 200:
            raise RuntimeError("青麦影视接口请求失败，HTTP %s" % response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError("青麦影视接口没有返回有效 JSON") from None
        if not isinstance(result, dict):
            raise RuntimeError("青麦影视接口响应结构无效")
        return result

    def _login(self):
        """获取匿名设备会话，失败时不打印设备标识或会话凭据。"""
        self.init()
        try:
            response = self.post(
                SITE_URL + "/api/auth/deviceIdLogin",
                params={"deviceId": self._device_id},
                headers=self._headers(),
                timeout=15,
            )
        except Exception:
            raise RuntimeError("青麦影视匿名设备登录请求失败") from None
        result = self._read_response(response)
        token = result.get("data")
        if result.get("code") != 200 or not isinstance(token, str) or not token:
            raise RuntimeError("青麦影视匿名设备登录失败")
        self._token = token

    def _request(self, route, payload):
        """仅在原站明确返回会话失效时刷新一次，其余失败交给云助手提示。"""
        self.init()
        if not self._token:
            self._login()
        for attempt in range(2):
            headers = self._headers()
            headers.update({"token": self._token, "deviceId": self._device_id})
            try:
                response = self.post(
                    SITE_URL + route, json=payload, headers=headers, timeout=15
                )
            except Exception:
                raise RuntimeError("青麦影视接口网络请求失败") from None
            result = self._read_response(response)
            if result.get("code") == 3 and attempt == 0:
                self._login()
                continue
            if result.get("code") != 200:
                raise RuntimeError("青麦影视接口返回错误码 %s" % result.get("code"))
            data = result.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("青麦影视接口数据结构无效")
            return data
        raise RuntimeError("青麦影视设备会话已失效")

    def _page_number(self, value):
        """分页只接受正整数，不把无效页码发送到源站。"""
        number = str(value)
        if not number.isdigit() or int(number) < 1:
            raise ValueError("青麦影视页码必须是正整数")
        return int(number)

    def _cards(self, records, category_hint=""):
        """保留源站分类和影片 ID；未识别分类不冒充任何已有分类。"""
        cards = []
        for record in records:
            if not isinstance(record, dict):
                continue
            category_id = str(record.get("typeId") or category_hint)
            movie_id = str(record.get("id") or "")
            if category_id not in CATEGORY_NAMES or not movie_id:
                continue
            episode_count = record.get("totalEpisode")
            remarks = record.get("remarks") or "高清"
            if not record.get("remarks") and isinstance(episode_count, int) and episode_count > 1:
                remarks = "全集%s集" % episode_count
            cards.append({
                "vod_id": "%s@@%s" % (category_id, movie_id),
                "type_id": category_id,
                "type_name": CATEGORY_NAMES[category_id],
                "vod_name": record.get("name") or "",
                "vod_pic": record.get("cover") or "",
                "vod_remarks": remarks,
                "vod_year": str(record.get("year") or ""),
            })
        return cards

    def _page_result(self, data, page, category_hint=""):
        """使用原站 total/pages，真实页码与返回内容始终一致。"""
        records = data.get("records")
        if not isinstance(records, list):
            raise RuntimeError("青麦影视目录缺少 records 列表")
        total = data.get("total")
        if not isinstance(total, int) or total < 0:
            raise RuntimeError("青麦影视目录缺少有效总数")
        page_count = data.get("pages")
        if not isinstance(page_count, int) or page_count < 1:
            page_count = max(1, math.ceil(total / PAGE_SIZE))
        return {
            "list": self._cards(records, category_hint),
            "page": page,
            "pagecount": page_count,
            "limit": PAGE_SIZE,
            "total": total,
        }

    def homeContent(self, filter=False):
        """首次加载只返回真实分类，避免为分类选择额外抓取影片详情。"""
        return {"class": [
            {"type_id": category_id, "type_name": name}
            for category_id, name in CATEGORIES
        ]}

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        """分类页使用真实页码，电影分类 M16 保持标准电影目录语义。"""
        category_id = str(tid)
        if category_id not in CATEGORY_NAMES:
            raise ValueError("青麦影视分类不存在")
        page = self._page_number(pg)
        data = self._request("/api/v1/pc/screen/screenMovie", {
            "condition": {
                "typeId": category_id, "sreecnTypeEnum": "HOT", "source": "0"
            },
            "pageNum": page,
            "pageSize": PAGE_SIZE,
        })
        return self._page_result(data, page, category_id)

    def searchContent(self, key, quick=False, pg="1"):
        """通用宿主的第三个参数就是源站搜索页码，不重复返回第一页。"""
        page = self._page_number(pg)
        keyword = str(key).strip()
        if not keyword:
            return {"list": [], "page": page, "pagecount": 1, "limit": PAGE_SIZE, "total": 0}
        data = self._request("/api/v1/pc/search/searchMovie", {
            "condition": {"value": keyword},
            "pageNum": page,
            "pageSize": PAGE_SIZE,
        })
        return self._page_result(data, page)

    def _movie_identity(self, value):
        """解析稳定影片身份，播放地址或匿名设备会话不参与身份。"""
        parts = str(value).split("@@")
        if len(parts) != 2 or parts[0] not in CATEGORY_NAMES or not parts[1].isdigit():
            raise ValueError("青麦影视影片标识无效")
        return parts[0], parts[1]

    def _movie_details(self, category_id, movie_id, player_id, episode_id=None):
        """请求指定影片与线路；取流信息只能来自同一影片。"""
        data = self._request("/api/v1/pc/play/movieDetails", {
            "id": movie_id,
            "typeId": category_id,
            "playerId": player_id,
            "episodeId": episode_id,
        })
        if str(data.get("id")) != movie_id:
            raise RuntimeError("青麦影视返回了不匹配的影片")
        return data

    def _line_name(self, player):
        """原站线路 ID 保证唯一，同名线路也能作为不同媒体版本显示。"""
        label = str(player.get("moviePlayerName") or "线路")
        label = re.sub(r"[#$\r\n]", " ", label).strip() or "线路"
        return "青麦影视 · %s (%s)" % (label, player["id"])

    def _episode_name(self, episode):
        """保留源站集数，避免不同线路排列变化后串集。"""
        number = str(episode.get("episodeNum") or "")
        label = str(episode.get("episode") or "")
        label = re.sub(r"[#$\r\n]", " ", label).strip()
        # 源站的幕后版、花絮也可能带 episodeNum=1，不能合并成普通第 1 集。
        if label and not re.fullmatch(r"(?:第\s*)?\d+\s*(?:集|话|期)?", label):
            return label
        if number.isdigit() and int(number) > 0:
            return "第%s集" % int(number)
        if not label:
            raise RuntimeError("青麦影视分集缺少名称和集数")
        return label

    def _line_episodes(self, data, category_id, movie_id, player_id):
        """每集保存影片、线路与原始分集 ID，不保存临时播放 URL。"""
        episodes = data.get("episodeList")
        if not isinstance(episodes, list):
            raise RuntimeError("青麦影视线路缺少分集列表")
        entries = []
        names = set()  # 关键变量：重复集数必须明确报错，不能随意选到另一集。
        for episode in episodes:
            if not isinstance(episode, dict) or not str(episode.get("id") or "").isdigit():
                raise RuntimeError("青麦影视分集身份无效")
            name = self._episode_name(episode)
            if name in names:
                raise RuntimeError("青麦影视同一线路的分集名称重复")
            names.add(name)
            entries.append("%s$%s@@%s@@%s@@%s" % (
                name, category_id, movie_id, player_id, episode["id"]
            ))
        return "#".join(entries)

    def detailContent(self, ids):
        """完整展开源站真实线路，协议分集与实时搜索使用相同详情。"""
        movie_identity = ids[0] if isinstance(ids, list) and ids else ids
        category_id, movie_id = self._movie_identity(movie_identity)
        description = self._request("/api/v1/pc/play/movieDesc", {
            "id": movie_id, "playerId": 90, "typeId": category_id
        })
        if str(description.get("id")) != movie_id:
            raise RuntimeError("青麦影视返回了不匹配的影片简介")
        # 关键变量：90 只用于查询源站默认线路，随后保存原站明确返回的实际线路 ID。
        initial = self._movie_details(category_id, movie_id, 90)
        players = initial.get("moviePlayerList")
        if not isinstance(players, list):
            raise RuntimeError("青麦影视详情缺少线路列表")
        flags, groups = [], []
        player_ids = set()
        for player in players:
            if not isinstance(player, dict) or not str(player.get("id") or "").isdigit():
                raise RuntimeError("青麦影视线路身份无效")
            player_id = int(player["id"])
            if player_id in player_ids:
                continue
            player_ids.add(player_id)
            data = initial if str(initial.get("playerId")) == str(player_id) else self._movie_details(
                category_id, movie_id, player_id
            )
            if str(data.get("playerId")) != str(player_id):
                raise RuntimeError("青麦影视返回了不匹配的播放线路")
            group = self._line_episodes(data, category_id, movie_id, player_id)
            if group:
                flags.append(self._line_name(player))
                groups.append(group)
        item = {
            "vod_id": str(movie_identity),
            "type_id": category_id,
            "type_name": CATEGORY_NAMES[category_id],
            "vod_name": description.get("name") or initial.get("name") or "",
            "vod_pic": description.get("cover") or "",
            "vod_year": str(description.get("year") or ""),
            "vod_area": description.get("area") or "",
            "vod_score": str(description.get("score") or ""),
            "vod_actor": description.get("star") or "",
            "vod_director": description.get("director") or "",
            "vod_content": description.get("introduce") or "",
            "vod_class": description.get("classify") or "",
            "vod_play_from": "$$$".join(flags),
            "vod_play_url": "$$$".join(groups),
        }
        return {"list": [item]}

    def playerContent(self, flag, id, vipFlags=None):
        """只返回指定影片、线路、分集的 HTTP 媒体地址，禁止跨集回退。"""
        parts = str(id).split("@@")
        if len(parts) != 4 or not all(value.isdigit() for value in parts[1:]):
            raise ValueError("青麦影视播放标识无效")
        category_id, movie_id = self._movie_identity("@@".join(parts[:2]))
        player_id, episode_id = int(parts[2]), int(parts[3])
        data = self._movie_details(category_id, movie_id, player_id, episode_id)
        if str(data.get("playerId")) != str(player_id):
            raise RuntimeError("青麦影视当前播放线路已经不可用")
        current_episode = data.get("currentEpisode")
        if not isinstance(current_episode, dict) or str(current_episode.get("id")) != str(episode_id):
            raise RuntimeError("青麦影视没有返回所选分集")
        if current_episode.get("lock") is True:
            raise RuntimeError("青麦影视此集需要源站授权")
        url = str(data.get("url") or "").strip()
        # 关键变量：原站在媒体地址外附加线路名和提示文字，按其实际封装格式取地址。
        url = re.split(r"[～~]", url, maxsplit=1)[0]
        if "*" in url:
            url = url.split("*", 1)[1]
        parsed = urlsplit(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            raise RuntimeError("青麦影视当前线路未返回有效媒体地址")
        result = {
            "parse": 0,
            "url": url.strip(),
            "header": {"User-Agent": USER_AGENT, "Referer": SITE_URL + "/"},
        }
        # 无扩展名的地址保持未知，交给云助手已有正文格式识别与 HLS 中转处理。
        if parsed.path.lower().endswith(".m3u8"):
            result["type"] = "hls"
        elif parsed.path.lower().endswith(".mp4"):
            result["type"] = "mp4"
        return result
