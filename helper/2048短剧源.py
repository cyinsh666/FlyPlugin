#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
══════════════════════════════════════════════════════════════════
9g/md 系列 Python Spider
适用生态：TVBox / 影视仓 / py-drpy / 猫影视
作者交流群：TG群 https://t.me/+3t5XRPPo9mlkZDRl
功能特性：普通长视频全分类 + 🎬 AI短剧专区（全集独立播放流精准选集）
══════════════════════════════════════════════════════════════════
"""

import json
import re
import urllib.parse
from typing import Dict, List, Any

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase:
        pass



class Spider(SpiderBase):
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = '2048短剧'

    siteUrl = "https://mdcmai4.xyz"
    api_categories = "/api/v1/categories?type=video"
    api_videos = "/api/v1/videos"
    # 关键变量：公开前端 getVideoById 使用的真实详情接口。
    api_video_detail = "/api/v1/videos/{id}"
    api_short_dramas = "/api/v1/short-dramas"
    api_short_drama_detail = "/api/v1/short-dramas/{id}?productId=1"
    api_search = "/api/v1/videos/search"
    api_m3u8_proxy = "/api/v1/m3u8/proxy?path="
    api_img_proxy = "/api/v1/image/proxy?path="

    SHORT_DRAMA_TID = "short_drama_ai"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://mdcmai4.xyz/",
        "Origin": "https://mdcmai4.xyz",
        "Accept": "application/json, text/plain, */*",
    }

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '2048短剧'

    def init(self, extend: str = "") -> bool:
        """允许插件配置原站入口，其他请求头随入口同步。"""
        configured = str(extend or "").strip()
        if configured.startswith("{"):
            value = json.loads(configured)
            configured = str(value.get("host") or value.get("siteUrl") or "").strip()
        if configured:
            address = urllib.parse.urlsplit(configured)
            if address.scheme not in ("http", "https") or not address.netloc:
                return self.sourceFailure("源站入口地址无效")
            self.siteUrl = configured.rstrip("/")
        self.headers = dict(self.headers)
        self.headers["Referer"] = self.siteUrl + "/"
        self.headers["Origin"] = self.siteUrl
        return True

    def isVideoFormat(self, url: str) -> bool:
        """识别原站常用媒体扩展名。"""
        return ".m3u8" in url or ".mp4" in url

    def manualVideoCheck(self) -> bool:
        """媒体取址交由标准播放接口处理。"""
        return False

    def destroy(self):
        """插件没有额外的后台资源需要释放。"""
        pass

    # ───── 网络请求封装 ─────
    def _fetch_json(self, url):
        """读取原站公开 JSON，HTTP 与业务失败均不能表现为空列表。"""
        try:
            response = self.fetch(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("code") != 200:
                return self.sourceFailure("源站接口返回业务失败")
            return result
        except Exception:
            return self.sourceFailure("源站 JSON 接口读取失败")

    # ───── URL 规范化组装 ─────
    def _format_cover(self, cover_path: str) -> str:
        """格式化封面图片地址"""
        if not cover_path:
            return ""
        if cover_path.startswith("http"):
            return cover_path
        if cover_path.startswith("/uploads/") or cover_path.startswith("/api/"):
            return f"{self.siteUrl}{cover_path}"
        encoded_path = urllib.parse.quote(cover_path, safe="")
        return f"{self.siteUrl}{self.api_img_proxy}{encoded_path}"

    def _format_play_url(self, video_path: str) -> str:
        """格式化 m3u8 播放地址"""
        if not video_path:
            return ""
        if video_path.startswith("http"):
            return video_path
        if video_path.startswith("/api/v1/m3u8/proxy"):
            return f"{self.siteUrl}{video_path}"
        encoded_path = urllib.parse.quote(video_path, safe="")
        return f"{self.siteUrl}{self.api_m3u8_proxy}{encoded_path}"

    def _format_duration(self, seconds: int) -> str:
        """秒数转为 分:秒"""
        if not seconds:
            return ""
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    # ───── TVBox 核心接口 ─────

    def homeContent(self, filter: bool = False) -> Dict[str, Any]:
        """首页：加入【AI短剧】大分类以及所有长视频分类"""
        classes = [
            {"type_id": self.SHORT_DRAMA_TID, "type_name": "🔥 AI短剧"}
        ]

        url = f"{self.siteUrl}{self.api_categories}"
        res = self._fetch_json(url)

        if res.get("code") == 200 and isinstance(res.get("data"), list):
            for cat in res["data"]:
                if cat.get("enabled", True):
                    classes.append({
                        "type_id": str(cat.get("id")),
                        "type_name": cat.get("name", "未知分类")
                    })

        return {"class": classes}

    def categoryContent(self, tid: str, pg: str, filter: bool, extend: Dict) -> Dict[str, Any]:
        """分类列表：自动分流处理 AI 短剧与普通长视频"""
        page = int(pg) if pg else 1
        videos = []
        pagecount = page
        total = 0

        # 分流 1：AI 短剧独立列表
        if str(tid) == self.SHORT_DRAMA_TID:
            url = f"{self.siteUrl}{self.api_short_dramas}?productId=1&sortBy=heat&page={page}&size=12"
            res = self._fetch_json(url)
            if res.get("code") == 200:
                data = res.get("data", {})
                pagecount = data.get("totalPages", page)
                total = data.get("total", 0)

                for item in data.get("items", []):
                    ep_cnt = item.get("episodeCount", 1)
                    rating = item.get("rating", 0.0)
                    videos.append({
                        "vod_id": "drama_%s" % item.get("id"),
                        "vod_name": item.get("title", ""),
                        "type_id": self.SHORT_DRAMA_TID if str(tid) == self.SHORT_DRAMA_TID else str(item.get("categoryId") or tid),
                        "vod_pic": self._format_cover(item.get("coverUrl", "")),
                        "vod_remarks": f"评分:{rating} | 共{ep_cnt}集"
                    })
        # 分流 2：普通长视频分类
        else:
            url = f"{self.siteUrl}{self.api_videos}?page={page}&size=24&categoryId={tid}"
            res = self._fetch_json(url)
            if res.get("code") == 200:
                data = res.get("data", {})
                pagecount = data.get("totalPages", page)
                total = data.get("total", 0)

                for item in data.get("items", []):
                    videos.append({
                        "vod_id": "video_%s" % item.get("id"),
                        "vod_name": item.get("title", ""),
                        "type_id": self.SHORT_DRAMA_TID if str(tid) == self.SHORT_DRAMA_TID else str(item.get("categoryId") or tid),
                        "vod_pic": self._format_cover(item.get("coverUrl", "")),
                        "vod_remarks": self._format_duration(item.get("durationSec", 0)) or item.get("categoryName", "")
                    })

        return {
            "list": videos,
            "page": page,
            "pagecount": pagecount,
            "limit": 12 if str(tid) == self.SHORT_DRAMA_TID else 24,
            "total": total
        }

    def _detail_data(self, kind, identifier):
        """根据稳定源站 ID 重新读取详情，不依赖上次浏览的内存记录。"""
        if not re.fullmatch(r"[0-9]+", str(identifier)):
            return self.sourceFailure("源影片标识格式无效")
        endpoint = self.api_short_drama_detail if kind == "drama" else self.api_video_detail
        data = self._fetch_json(self.siteUrl + endpoint.format(id=identifier)).get("data")
        if not isinstance(data, dict):
            return self.sourceFailure("源站详情结构无效")
        return data

    def detailContent(self, ids):
        """采用真实详情和分集，条目标识与播放标识均不保存临时地址。"""
        if not ids:
            return {"list": []}
        item_id = str(ids[0])
        identity = re.fullmatch(r"(drama|video)_([0-9]+)", item_id)
        if not identity:
            return self.sourceFailure("源影片标识格式无效")
        kind, identifier = identity.groups()
        data = self._detail_data(kind, identifier)
        title = str(data.get("title") or "")
        entries = []
        if kind == "drama":
            if not isinstance(data.get("episodes"), list):
                return self.sourceFailure("源站未返回真实分集")
            for episode in data["episodes"]:
                # 关键变量：集号与可用状态均来自接口，不根据 episodeCount 编造剧集。
                number = episode.get("episodeNo")
                if number is not None and episode.get("videoUrl") and re.fullmatch(r"[0-9]+", str(number)):
                    entries.append("第%s集$drama_%s_%s" % (number, identifier, number))
            source_name = "AI短剧专线"
            category = self.SHORT_DRAMA_TID
        else:
            if data.get("videoUrl"):
                entries.append("正片$video_" + identifier)
            source_name = "专线播放"
            category = str(data.get("categoryId") or "")
        if not title or not entries:
            return self.sourceFailure("源站详情没有可播放内容")
        return {"list": [{"vod_id": item_id, "vod_name": title, "type_id": category,
                          "vod_pic": self._format_cover(str(data.get("coverUrl") or "")),
                          "vod_content": str(data.get("description") or ""),
                          "vod_play_from": source_name, "vod_play_url": "#".join(entries)}]}

    def searchContent(self, key, quick=False, pg="1"):
        """传递搜索页码并使用原站分页总数。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, 24, 0)
        data = self._fetch_json("%s%s?page=%s&size=24&q=%s" % (self.siteUrl, self.api_search, page, urllib.parse.quote(str(key)))).get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return self.sourceFailure("源站搜索结构无效")
        items = []
        for item in data["items"]:
            items.append({"vod_id": "video_%s" % item.get("id"),
                          "vod_name": item.get("title") or "", "vod_pic": self._format_cover(item.get("coverUrl") or ""),
                          "type_id": str(item.get("categoryId") or ""),
                          "vod_remarks": self._format_duration(item.get("durationSec") or 0)})
        return self.pageResult(items, page, data.get("totalPages"), 24, data.get("total"))

    def playerContent(self, flag: str, id: str, vipFlags: str) -> Dict[str, Any]:
        """播放前按真实作品及集号重新取址，避免目录缓存中的地址过期。"""
        identity = re.fullmatch(r"(video|drama)_([0-9]+)(?:_([0-9]+))?", str(id))
        if not identity:
            return self.sourceFailure("播放标识格式无效")
        kind, identifier, number = identity.groups()
        data = self._detail_data(kind, identifier)
        address = ""
        if kind == "video" and number is None:
            address = str(data.get("videoUrl") or "")
        elif kind == "drama" and number is not None:
            for episode in data.get("episodes", []):
                if str(episode.get("episodeNo")) == number:
                    address = str(episode.get("videoUrl") or "")
                    break
        if not address:
            return self.sourceFailure("源站未返回当前分集的播放地址")
        result = {"parse": 0, "playUrl": "", "url": self._format_play_url(address),
                  "header": {"User-Agent": self.headers["User-Agent"],
                             "Referer": self.siteUrl + "/", "Origin": self.siteUrl}}
        # 关键变量：原始媒体路径的扩展名可识别格式，代理入口本身没有扩展名。
        media_path = urllib.parse.urlsplit(address).path.lower()
        if media_path.endswith(".m3u8"):
            result["type"] = "m3u8"
        elif media_path.endswith(".mp4"):
            result["type"] = "mp4"
        return result

    def localProxy(self, param: Dict) -> List[Any]:
        """当前源使用原站公开媒体代理，没有本地代理资源。"""
        return [404, "text/plain", ""]
