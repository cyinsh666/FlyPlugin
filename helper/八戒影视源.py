# -*- coding: utf-8 -*-
"""八戒影视独立 CatVod Python 源：真实分类、搜索分页与每条线路的真实分集。"""

import json
import re
import uuid
from urllib.parse import urlsplit

from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """沿用公开 App 接口，不根据集数计数制造分集或伪造用户权限。"""

    display_name = "八戒影视"
    # 关键变量：原资料提供的公开域名配置入口；用户可在扩展配置中指定 host。
    DOMAIN_CONFIG_URL = "http://osstexll.oss-rg-china-mainland.aliyuncs.com/domainPath.json"

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '八戒影视'

    def init(self, extend=""):
        """建立本实例会话；仅在没有本人授权时请求原站正常游客身份。"""
        if isinstance(extend, dict):
            config = extend
        elif isinstance(extend, str) and extend.strip():
            config = {"host": extend.strip()} if extend.strip().startswith(("http://", "https://")) else json.loads(extend)
        else:
            config = {}
        if not isinstance(config, dict):
            return self.sourceFailure("八戒扩展配置必须是 JSON 对象或站点地址")
        self.host = str(config.get("host") or "").strip().rstrip("/")
        # 关键变量：每个源实例独立游客设备标识，避免所有安装共用原脚本的固定 ID。
        self.headers = {"User-Agent": "okhttp/4.12.0", "Content-Type": "application/json;charset=UTF-8",
                        "Accept-Encoding": "gzip", "client": "app", "deviceType": "Android",
                        "deviceId": str(config.get("deviceId") or uuid.uuid4().hex[:16]),
                        "token": str(config.get("token") or "")}
        if not self.host:
            response = self.fetch(self.DOMAIN_CONFIG_URL, headers={"User-Agent": self.headers["User-Agent"]}, timeout=15)
            response.raise_for_status()
            config_result = response.json()
            addresses = config_result.get("url") if isinstance(config_result, dict) else None
            if not isinstance(addresses, list) or not addresses:
                return self.sourceFailure("八戒源站没有返回可用域名，请更新源站地址")
            self.host = str(addresses[0]).rstrip("/")
        if urlsplit(self.host).scheme not in ("http", "https") or not urlsplit(self.host).hostname:
            return self.sourceFailure("八戒源站地址无效")
        self.userid = str(config.get("userId") or "")
        if self.headers["token"]:
            if not self.userid:
                return self.sourceFailure("本人原站 token 需要同时提供对应 userId")
        else:
            visitor = self._request("/api/v1/app/user/visitorInfo", method="GET").get("data")
            if not isinstance(visitor, dict) or not visitor.get("id") or not visitor.get("token"):
                return self.sourceFailure("八戒原站未返回正常游客身份")
            self.userid = str(visitor["id"])
            self.headers["token"] = str(visitor["token"])
        return None

    def _request(self, path, payload=None, method="POST", params=None):
        """调用原站正常接口；业务或网络错误明确抛出且不记录授权内容。"""
        try:
            if method == "GET":
                response = self.fetch(self.host + path, params=params, headers=self.headers, timeout=15)
            else:
                response = self.post(self.host + path, json=payload, headers=self.headers, timeout=15)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("code", 200) not in (0, 200):
                return self.sourceFailure("八戒原站拒绝请求或需要原站授权")
            return result
        except Exception:
            return self.sourceFailure("八戒原站接口读取失败或授权不足")

    def _card(self, item):
        """只保留原站真实影片身份与分类，搜索结果不猜测归属。"""
        if not isinstance(item, dict) or item.get("id") is None or not item.get("name"):
            return self.sourceFailure("八戒原站影片摘要结构无效")
        result = {"vod_id": str(item["id"]), "vod_name": str(item["name"]),
                  "vod_pic": str(item.get("cover") or ""), "vod_year": str(item.get("year") or ""),
                  "vod_remarks": str(item.get("area") or ""), "vod_content": str(item.get("desc") or "")}
        if item.get("typeId") is not None:
            result["type_id"] = str(item["typeId"])
        return result

    def _page(self, data, page):
        """使用接口 records、pages、total 字段生成分页，不预截断候选项。"""
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            return self.sourceFailure("八戒原站列表结构无效")
        return self.pageResult([self._card(item) for item in data["records"]], page,
                               data.get("pages"), data.get("size") or 40, data.get("total"))

    def homeContent(self, filter=False):
        """返回源站筛选分类的真实 ID。"""
        data = self._request("/api/v1/app/screen/screenType").get("data")
        if not isinstance(data, list):
            return self.sourceFailure("八戒原站分类结构无效")
        return {"class": [{"type_id": str(item["id"]), "type_name": str(item["name"])}
                          for item in data if isinstance(item, dict) and item.get("id") is not None and item.get("name")]}

    def homeVideoContent(self):
        """首页摘要由分类目录提供，不为推荐额外并发请求影片详情。"""
        return {"list": []}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        """查询指定分类与页码，保留原站筛选语义。"""
        page = max(1, int(pg or 1))
        values = extend if isinstance(extend, dict) else {}
        condition = {"classify": str(values.get("classify") or ""), "region": str(values.get("region") or ""),
                     "sreecnTypeEnum": str(values.get("sort") or "NEWEST"), "typeId": str(tid),
                     "year": str(values.get("year") or "")}
        data = self._request("/api/v1/app/screen/screenMovie", {"condition": condition, "pageNum": page, "pageSize": 40}).get("data")
        result = self._page(data, page)
        for item in result["list"]:
            item["type_id"] = str(tid)
        return result

    def searchContent(self, key, quick=False, pg="1"):
        """传递原站全局搜索页码，未返回类型的作品保持无归属状态。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, 40, 0)
        data = self._request("/api/v1/app/search/searchMovie", {"condition": {"value": str(key)}, "pageNum": page, "pageSize": 40}).get("data")
        return self._page(data, page)

    def _line_detail(self, video_id, player_id=None):
        """按实际线路读取源站 episodeList，不按 episodeTotal 制造虚拟集。"""
        payload = {"id": str(video_id), "source": 0, "typeId": "M17", "userId": self.userid}
        if player_id is not None:
            payload["playerId"] = player_id
        data = self._request("/api/v1/app/play/movieDetails", payload).get("data")
        if not isinstance(data, dict):
            return self.sourceFailure("八戒原站详情结构无效")
        return data

    def detailContent(self, ids):
        """分别读取线路真实选集，只有真实 episodeId 才能进入播放列表。"""
        if not ids:
            return {"list": []}
        video_id = str(ids[0])
        data = self._line_detail(video_id)
        current_id = data.get("playerId")
        players = data.get("moviePlayerList")
        if not isinstance(players, list):
            players = []
        if current_id is not None and not any(str(item.get("id")) == str(current_id) for item in players if isinstance(item, dict)):
            players = [{"id": current_id, "moviePlayerName": "原站线路"}] + players
        sources, playlists = [], []
        seen_lines = set()
        for player in players:
            if not isinstance(player, dict) or player.get("id") is None:
                continue
            player_id = str(player["id"])
            if player_id in seen_lines:
                continue
            seen_lines.add(player_id)
            line = data if player_id == str(current_id) else self._line_detail(video_id, player["id"])
            episodes = line.get("episodeList")
            if not isinstance(episodes, list):
                continue
            entries = []
            type_id = str(line.get("typeId") or data.get("typeId") or "M17")
            for index, episode in enumerate(episodes):
                if not isinstance(episode, dict) or episode.get("id") is None:
                    continue
                # 关键变量：稳定播放标识包含真实影片、线路、分集 ID 与原站内容类型。
                play_id = "@".join((video_id, player_id, str(episode["id"]), "episode", type_id))
                name = str(episode.get("episode") or episode.get("name") or "第%s集" % (index + 1)).replace("$", " ").replace("#", " ")
                entries.append(name + "$" + play_id)
            if entries:
                sources.append(str(player.get("moviePlayerName") or "原站线路").replace("$", " "))
                playlists.append("#".join(entries))
        if not playlists:
            return self.sourceFailure("八戒原站未返回真实分集，不能按总集数构造选集")
        description = self._request("/api/v1/app/play/movieDesc", {"id": video_id, "typeId": "M17"}).get("data")
        description = description if isinstance(description, dict) else {}
        return {"list": [{"vod_id": video_id, "vod_name": str(description.get("name") or data.get("name") or ""),
                          "vod_pic": str(description.get("cover") or data.get("cover") or ""),
                          "vod_content": str(description.get("introduce") or data.get("introduce") or ""),
                          "vod_year": str(description.get("year") or data.get("year") or ""),
                          "vod_area": str(description.get("area") or data.get("area") or ""),
                          "type_name": str(description.get("classify") or data.get("classify") or ""),
                          "vod_play_from": "$$$".join(sources), "vod_play_url": "$$$".join(playlists)}]}

    def playerContent(self, flag, id, vipFlags=None):
        """仅使用选中的真实 episodeId 请求原站授权取址，不切换到别集。"""
        parts = str(id).split("@")
        if len(parts) != 5 or parts[3] != "episode":
            return self.sourceFailure("八戒播放标识不是实际分集")
        video_id, player_id, episode_id, _, type_id = parts
        payload = {"episodeId": episode_id, "id": video_id, "playerId": player_id,
                   "source": 0, "typeId": type_id, "userId": self.userid}
        data = self._request("/api/v1/app/play/movieDetails", payload).get("data")
        if not isinstance(data, dict) or not isinstance(data.get("url"), str) or not data["url"]:
            return self.sourceFailure("八戒原站未授权此集或未返回播放信息")
        parsed = self._request("/api/v1/app/play/analysisMovieUrl", method="GET",
                               params={"playerUrl": data["url"], "playerId": data.get("playerId") or player_id}).get("data")
        if isinstance(parsed, dict):
            parsed = parsed.get("url")
        if not isinstance(parsed, str) or urlsplit(parsed).scheme not in ("http", "https"):
            return self.sourceFailure("八戒原站未返回有效播放地址")
        direct = bool(re.search(r"\.(m3u8|mp4|mkv|webm|flv)(?:\?|$)", parsed, re.I))
        return {"parse": 0 if direct else 1, "playUrl": "", "url": parsed,
                "header": {"User-Agent": self.headers["User-Agent"], "Referer": self.host + "/"}}
