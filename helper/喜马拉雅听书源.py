# -*- coding: utf-8 -*-
"""喜马拉雅免费有声书：目录只取摘要，详情取真实章节时长，播放即时刷新地址。"""

import html
import math
import re
from urllib.parse import urlsplit

import requests
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """使用喜马拉雅公开匿名接口，不接入账号、会员或付费解锁服务。"""

    cloud_helper_media_type = "audiobook"
    display_name = "喜马拉雅听书"
    SITE = "https://m.ximalaya.com"
    MOBILE_API = "https://mobile.ximalaya.com"
    SEARCH_API = "https://search.ximalaya.com/front/v1"
    PAGE_SIZE = 50
    TRACK_PAGE_SIZE = 200
    CATEGORIES = (
        ("youshengshu", "有声书"), ("ertong", "儿童"), ("xiangsheng", "相声评书"),
        ("guangbojv", "广播剧"), ("lishi", "历史"), ("jiaoyu", "教育"),
    )

    def __init__(self):
        """建立当前调用的匿名会话，不请求首页、不保存账号或临时音频地址。"""
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": self.SITE + "/",
            "Accept": "application/json, text/plain, */*",
        })

    def init(self, extend=""):
        """不在导入阶段读取分类或全书章节。"""
        return ""

    def getName(self):
        """提供宿主可静态识别的中文插件名称。"""
        return "喜马拉雅听书"

    def _request_json(self, url, params=None):
        """只接受成功的公开 JSON 响应，错误信息不包含临时地址和原站响应正文。"""
        try:
            response = self.session.get(url, params=params, timeout=15)
        except requests.RequestException:
            raise RuntimeError("喜马拉雅公开接口网络请求失败，请稍后重试") from None
        if response.status_code != 200:
            raise RuntimeError("喜马拉雅公开接口请求失败，HTTP %s" % response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("喜马拉雅公开接口没有返回有效 JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError("喜马拉雅公开接口返回结构无效")
        code = payload.get("ret")
        if code not in (None, 0, "0"):
            if code in (303, "303") or payload.get("needLogin") is True:
                raise RuntimeError("喜马拉雅当前公开接口要求登录，本插件仅支持匿名免费内容")
            raise RuntimeError("喜马拉雅公开接口暂不可用，请稍后重试")
        return payload

    @staticmethod
    def _page(value):
        """Spider 页号从一开始，仅接受正整数。"""
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _source_id(value, kind):
        """永久身份使用原站数字 ID，拒绝把临时媒体地址作为章节身份。"""
        text = str(value or "")
        match = re.fullmatch(r"xm:%s:(\d+)" % re.escape(kind), text)
        if match:
            return match.group(1)
        if re.fullmatch(r"\d+", text):
            return text
        raise RuntimeError("喜马拉雅%s标识无效" % ("书籍" if kind == "album" else "章节"))

    @staticmethod
    def _clean(value):
        """清理检索高亮标签，不执行页面内容。"""
        return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()

    @staticmethod
    def _cover(value):
        """将原站封面路径组成图片地址，不读取图片或详情。"""
        text = str(value or "")
        if text.startswith("//"):
            return "https:" + text
        if text.startswith(("https://", "http://")):
            return text
        return "https://imagev2.xmcdn.com/" + text.lstrip("/") if text else ""

    @staticmethod
    def _free_category(info):
        """分类必须同时明确标记非付费和非会员，缺少标记时不猜测为免费。"""
        return info.get("albumPayType") in (0, "0") and info.get("albumVipPayType") in (0, "0") and info.get("isVipFirst") is not True

    @staticmethod
    def _free_search(info):
        """搜索只显示原站明确标记为免费的专辑，不展示付费试听专辑。"""
        return info.get("is_paid") is False and info.get("is_vip") is False and info.get("isVipFree") is not True and info.get("price_type_id") in (0, "0")

    @staticmethod
    def _free_track(info):
        """章节必须明确非付费，会员抢先听和付费预览均不能进入可播放列表。"""
        return info.get("isPaid") is False and info.get("vipFirstStatus") in (0, "0", None) and info.get("isVipFree") is not True

    def homeContent(self, filter=False):
        """只返回静态分类，不预读书籍详情。"""
        return {"class": [{"type_id": key, "type_name": name} for key, name in self.CATEGORIES], "filters": {}}

    def homeVideoContent(self):
        """首页复用第一页摘要，音轨留到详情读取。"""
        return self.categoryContent("youshengshu", "1", False, {})

    def categoryContent(self, tid, pg, filter=False, extend=None):
        """保留上游真实分页范围，免费过滤后空页不能被误当成目录结束。"""
        if tid not in dict(self.CATEGORIES):
            raise RuntimeError("喜马拉雅分类不存在")
        page = self._page(pg)
        payload = self._request_json(self.SITE + "/m-revision/page/category/queryCategoryAlbumsByPage", {
            "sort": "0", "pageSize": self.PAGE_SIZE, "page": page, "categoryCode": tid,
        })
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("albumBriefDetailInfos"), list):
            raise RuntimeError("喜马拉雅分类接口缺少书籍列表")
        raw_items = data["albumBriefDetailInfos"]
        books = []
        for row in raw_items:
            info = row.get("albumInfo", {}) if isinstance(row, dict) else {}
            if not self._free_category(info):
                continue
            album_id = row.get("id") or info.get("id")
            if not str(album_id or "").isdigit():
                continue
            books.append({"vod_id": "xm:album:%s" % album_id, "vod_name": self._clean(info.get("title")),
                          "vod_pic": self._cover(info.get("cover")), "vod_remarks": "免费有声书"})
        # 关键变量：上游总数包含付费内容，不能将其冒充过滤后的免费作品总数。
        source_total = max(0, int(data.get("totalCount") or 0))
        source_pages = max(1, int(math.ceil(source_total / self.PAGE_SIZE)))
        return {"list": books, "page": page, "pagecount": source_pages, "limit": self.PAGE_SIZE,
                "raw_count": len(raw_items), "sourcePageCount": source_pages}

    def searchContent(self, key, quick=False, pg="1"):
        """执行原站全站远程搜索，接口失败直接报错，不用局部分类匹配代替搜索。"""
        page = self._page(pg)
        if not str(key or "").strip():
            return {"list": [], "page": page, "pagecount": 1, "limit": self.PAGE_SIZE, "raw_count": 0}
        payload = self._request_json(self.SEARCH_API, {
            "kw": str(key).strip(), "core": "album", "page": page, "rows": self.PAGE_SIZE,
            "condition": "relation", "device": "android",
        })
        data = payload.get("response")
        if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
            raise RuntimeError("喜马拉雅官方搜索暂不可用，请稍后重试")
        raw_items = data["docs"]
        books = []
        for info in raw_items:
            if not isinstance(info, dict) or not self._free_search(info):
                continue
            album_id = info.get("id")
            if not str(album_id or "").isdigit():
                continue
            books.append({"vod_id": "xm:album:%s" % album_id, "vod_name": self._clean(info.get("title")),
                          "vod_pic": self._cover(info.get("cover_path")), "vod_remarks": "免费有声书"})
        source_pages = max(1, int(data.get("totalPage") or math.ceil(max(0, int(data.get("numFound") or 0)) / self.PAGE_SIZE)))
        return {"list": books, "page": page, "pagecount": source_pages, "limit": self.PAGE_SIZE,
                "raw_count": len(raw_items), "sourcePageCount": source_pages}

    def _album_page(self, album_id, page):
        """按原站章节分页读取元数据，首屏使用零号入口，后续页使用真实 pageId。"""
        source_page = 0 if page == 1 else page
        return self._request_json(self.MOBILE_API + "/mobile/others/ca/album/track/%s/true/%s/%s" %
                                  (album_id, source_page, self.TRACK_PAGE_SIZE), {"albumId": album_id})

    @staticmethod
    def _audio_address(track):
        """只挑选公开普通音质，不读取需要会员的高品质地址。"""
        for field, default_mime in (("playPathAacv164", "audio/mp4"), ("playUrl64", "audio/mpeg"), ("playUrl32", "audio/mpeg")):
            address = str(track.get(field) or "")
            parsed = urlsplit(address)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            suffix = parsed.path.rsplit(".", 1)[-1].lower()
            mime = {"m4a": "audio/mp4", "mp4": "audio/mp4", "mp3": "audio/mpeg", "aac": "audio/aac"}.get(suffix, default_mime)
            return address, mime
        return "", ""

    def detailContent(self, ids):
        """完整读取免费章节及接口给出的秒数，不请求音频字节、不估算或补零时长。"""
        album_id = self._source_id(ids[0] if isinstance(ids, list) and ids else ids, "album")
        first = self._album_page(album_id, 1)
        album = first.get("album")
        tracks_page = first.get("tracks")
        if not isinstance(album, dict) or not isinstance(tracks_page, dict):
            raise RuntimeError("喜马拉雅专辑资料不完整")
        if album.get("isPaid") is not False or album.get("isVipFree") is True:
            raise RuntimeError("本插件仅支持喜马拉雅匿名免费专辑")
        # 关键变量：这里的时长直接来自同一物理 track 的 duration 秒，不能按书籍总时长均分。
        max_page = max(1, int(tracks_page.get("maxPageId") or 1))
        if max_page > 500:
            raise RuntimeError("喜马拉雅专辑章节分页过多，请缩小作品范围")
        audio_tracks = []
        seen = set()
        for page in range(1, max_page + 1):
            payload = first if page == 1 else self._album_page(album_id, page)
            info = payload.get("tracks")
            if not isinstance(info, dict) or not isinstance(info.get("list"), list):
                raise RuntimeError("喜马拉雅章节分页返回结构无效，请稍后重新读取详情")
            if page > 1 and not info["list"]:
                raise RuntimeError("喜马拉雅章节分页为空，请稍后重新读取详情")
            for track in info["list"]:
                if not isinstance(track, dict) or not self._free_track(track):
                    continue
                track_id = str(track.get("trackId") or "")
                if not track_id.isdigit() or track_id in seen:
                    continue
                try:
                    duration = float(track.get("duration"))
                except (TypeError, ValueError):
                    duration = 0
                if not math.isfinite(duration) or duration <= 0:
                    raise RuntimeError("喜马拉雅章节缺少真实时长，暂不能建立准确的全书时间轴")
                _, mime_type = self._audio_address(track)
                if not mime_type:
                    continue
                seen.add(track_id)
                audio_tracks.append({"id": track_id, "title": self._clean(track.get("title")) or "未命名章节",
                                     "duration": duration, "mimeType": mime_type, "flag": "audiobook",
                                     "playbackId": "xm:track:%s" % track_id})
        if not audio_tracks:
            raise RuntimeError("喜马拉雅专辑当前没有可匿名播放的免费音频章节")
        title = self._clean(album.get("title"))
        if not title:
            raise RuntimeError("喜马拉雅专辑缺少作品名称")
        return {"list": [{"vod_id": "xm:album:%s" % album_id, "vod_name": title,
                          "vod_pic": self._cover(album.get("coverLarge") or album.get("coverMiddle")),
                          "vod_content": self._clean(album.get("intro")), "vod_remarks": "共%s个免费章节" % len(audio_tracks),
                          "vod_play_from": "audiobook", "vod_audio_tracks": audio_tracks,
                          "vod_play_url": "#".join("%s$%s" % (track["title"].replace("$", "￥").replace("#", "﹟"), track["playbackId"]) for track in audio_tracks)}]}

    def audioChapterPage(self, book_id, page=1):
        """原生听书每次只读取一页目录，未知时长保留为空，不串行读取全书。"""
        album_id = self._source_id(book_id, "album")
        page = self._page(page)
        payload = self._album_page(album_id, page)
        album, info = payload.get("album"), payload.get("tracks")
        if not isinstance(album, dict) or not isinstance(info, dict) or not isinstance(info.get("list"), list):
            raise RuntimeError("喜马拉雅章节分页返回结构无效")
        if album.get("isPaid") is not False or album.get("isVipFree") is True:
            raise RuntimeError("本插件仅支持喜马拉雅匿名免费专辑")
        page_count = max(1, int(info.get("maxPageId") or 1))
        if page_count > 500 or page > page_count:
            raise RuntimeError("喜马拉雅章节分页范围无效")
        tracks = []
        seen = set()
        for index, track in enumerate(info["list"]):
            if not isinstance(track, dict) or not self._free_track(track):
                continue
            track_id = str(track.get("trackId") or "")
            if not track_id.isdigit() or track_id in seen:
                raise RuntimeError("喜马拉雅章节分页身份无效或重复")
            seen.add(track_id)
            _, mime_type = self._audio_address(track)
            if not mime_type:
                continue
            try:
                duration = float(track.get("duration"))
            except (TypeError, ValueError):
                duration = None
            if duration is not None and (not math.isfinite(duration) or duration <= 0):
                duration = None
            tracks.append({"id": track_id, "title": self._clean(track.get("title")) or "未命名章节",
                           "duration": duration, "mimeType": mime_type, "flag": "audiobook",
                           "order": (page - 1) * self.TRACK_PAGE_SIZE + index + 1,
                           "playbackId": "xm:track:%s" % track_id})
        title = self._clean(album.get("title"))
        if not title:
            raise RuntimeError("喜马拉雅专辑缺少作品名称")
        # 关键变量：公开页没有不可变快照令牌，不能凭一次翻页缺失便删除已知章节。
        return {"list": [{"vod_id": "xm:album:%s" % album_id, "vod_name": title,
                          "vod_pic": self._cover(album.get("coverLarge") or album.get("coverMiddle")),
                          "vod_content": self._clean(album.get("intro")), "vod_audio_tracks": tracks}],
                "page": page, "pagecount": page_count, "raw_count": len(info["list"]),
                "authoritative": False, "snapshot_id": None}

    def audioChapterPlayback(self, book_id, chapter_id):
        """永久书籍和章节身份直接取址，切章不依赖完整目录缓存。"""
        album_id = self._source_id(book_id, "album")
        return self._track_playback(chapter_id, album_id)

    def playerContent(self, flag, id, vipFlags=None):
        """按永久章节 ID 重新取公开地址，过期地址和付费试听不能冒充完整免费章节。"""
        return self._track_playback(id)

    def _track_playback(self, id, expected_album_id=None):
        """只读取当前章节公开信息；有原站专辑身份时同时核对书章归属。"""
        track_id = self._source_id(id, "track")
        payload = self._request_json(self.MOBILE_API + "/v1/track/baseInfo", {"device": "android", "trackId": track_id})
        if str(payload.get("trackId") or "") != track_id or not self._free_track(payload):
            raise RuntimeError("喜马拉雅章节已下架、改为付费或当前不允许匿名播放")
        if expected_album_id and payload.get("albumId") is not None and str(payload["albumId"]) != expected_album_id:
            raise RuntimeError("喜马拉雅章节不属于当前专辑")
        address, mime_type = self._audio_address(payload)
        if not address:
            raise RuntimeError("喜马拉雅章节暂未提供公开音频地址")
        return {"parse": 0, "url": address, "jx": 0, "mimeType": mime_type,
                "header": {"User-Agent": self.session.headers["User-Agent"], "Referer": self.SITE + "/"}}

    def isVideoFormat(self, url):
        """兼容 Spider 宿主的音频格式判断，书籍不伪装成影视内容。"""
        return bool(re.search(r"\.(?:m4a|mp3|aac|mp4)(?:\?|$)", str(url or ""), re.I))

    def manualVideoCheck(self):
        """播放直接使用原站匿名音频，不启动网页嗅探。"""
        return False
