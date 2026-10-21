# coding=utf-8
"""短剧大全独立 Spider：目录只取摘要，详情与分集取址按需读取。"""

import hashlib
import html
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """只解析源站公开 HTML，不执行网页脚本或依赖其他插件。"""

    display_name = "短剧大全"

    def init(self, extend=""):
        """准备请求头；源站没有真实题材分类，只公开全部短剧。"""
        self.site_url = "https://lssy.net"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": self.site_url + "/",
        }
        # 关键变量：分类身份固定，搜索结果也使用同一个真实的全量目录入口。
        self.category = {"type_id": "1", "type_name": "短剧大全"}

    def getName(self):
        """提供导入时可以由宿主 AST 读取的固定名称。"""
        return "短剧大全"

    def _read_page(self, address, params=None, allow_missing=False):
        """HTTP 与验证失败直接报错，避免把源站异常当成空库保存。"""
        response = self.fetch(address, params=params, headers=self.headers, timeout=15)
        if allow_missing and response.status_code == 404:
            return None
        response.raise_for_status()
        response.encoding = "utf-8"
        page_text = response.text
        if any(marker in page_text for marker in (
            "系统安全验证", "访问安全校验", "cf-chl-", "cf-error-details",
            "Just a moment...", "Attention Required!", "Verify you are human",
        )):
            raise RuntimeError("短剧大全源站要求额外验证，请稍后重试")
        return BeautifulSoup(page_text, "html.parser")

    def _page_number(self, value):
        """仅接受正整数页码，不静默回退到第一页。"""
        page = int(value or 1)
        if page < 1:
            raise ValueError("短剧大全分页页码必须为正整数")
        return page

    def _item_id(self, value):
        """从真实详情链接提取数字影片 ID，排除推荐外链和其他页面。"""
        address = urlsplit(urljoin(self.site_url + "/", value))
        if address.netloc.lower() != urlsplit(self.site_url).netloc:
            return ""
        if address.path != "/detail.php":
            return ""
        values = parse_qs(address.query).get("vid", [])
        return values[0] if len(values) == 1 and re.fullmatch(r"\d+", values[0]) else ""

    def _cards(self, soup):
        """只解析目录卡片，不访问详情页或把媒体链接带入列表。"""
        cards = []
        seen = set()
        for card in soup.select(".grid > .card"):
            link = card.select_one('a[href*="detail.php?"]')
            item_id = self._item_id(link.get("href", "")) if link else ""
            if not item_id or item_id in seen:
                continue
            title_node = card.select_one(".title")
            title = title_node.get_text("", strip=True) if title_node else ""
            if not title:
                raise RuntimeError("短剧大全源站目录缺少标题，请更新源脚本")
            seen.add(item_id)
            image = card.select_one("img")
            poster = (image.get("data-original") or image.get("src", "")) if image else ""
            episode_count = re.search(r"(\d+)\s*集", card.get_text(" ", strip=True))
            cards.append({
                "vod_id": item_id,
                "vod_name": title,
                "vod_pic": urljoin(self.site_url + "/", poster) if poster else "",
                "vod_remarks": episode_count.group(1) + "集" if episode_count else "",
                **self.category,
            })
        return cards

    def _pagination(self, soup):
        """只采用明确总数或尾页链接，不把分页窗口的最大页码当尾页。"""
        result = {}
        total_match = re.search(r"共找到\s*([\d,]+)\s*部", soup.get_text(" ", strip=True))
        if total_match:
            result["total"] = int(total_match.group(1).replace(",", ""))
        for link in soup.select("a[href]"):
            if link.get_text(strip=True) not in ("尾页", "末页", "最后一页", "最后页"):
                continue
            address = urlsplit(urljoin(self.site_url + "/", html.unescape(link["href"])))
            if address.netloc.lower() != urlsplit(self.site_url).netloc or address.path != "/":
                continue
            values = parse_qs(address.query).get("p", [])
            if len(values) == 1 and values[0].isdigit() and int(values[0]) > 0:
                result["pagecount"] = int(values[0])
                break
        return result

    def _list_page(self, page, keyword=""):
        """目录和实时搜索共用真实分页接口，仅输出当前页摘要。"""
        params = {"p": page}
        if keyword:
            params["keyword"] = keyword
        soup = self._read_page(self.site_url + "/", params)
        cards = self._cards(soup)
        pagination = self._pagination(soup)
        # 关键变量：空结果必须有源站自己的目录或空结果证据，未知结构明确失败。
        if not cards and "total" not in pagination:
            raise RuntimeError("短剧大全源站没有返回可识别的目录，请稍后重试或更新源脚本")
        if not cards and pagination.get("total", 0) > (page - 1) * 20:
            raise RuntimeError("短剧大全源站目录条目解析失败，请更新源脚本")
        result = {"list": cards, "page": page, "limit": 20}
        result.update(pagination)
        return result

    def homeContent(self, filter):
        """本地返回唯一真实分类，首轮目录由宿主按页同步。"""
        return {"class": [self.category.copy()], "list": []}

    def categoryContent(self, tid, pg, filter, extend):
        """每次读取一整页目录摘要，详情与分集都保持按需加载。"""
        if str(tid) != self.category["type_id"]:
            raise ValueError("短剧大全分类不存在")
        return self._list_page(self._page_number(pg))

    def searchContent(self, key, quick, pg="1"):
        """将原关键词传给源站搜索，不以推荐内容冒充搜索结果。"""
        keyword = str(key).strip()
        if not keyword:
            return {"list": [], "page": self._page_number(pg), "pagecount": 1, "limit": 20, "total": 0}
        return self._list_page(self._page_number(pg), keyword)

    def _media_address(self, value):
        """仅接受 HTTP(S) 的 HLS 或 MP4，解析页不能冒充媒体。"""
        value = html.unescape(value).replace("\\/", "/").strip()
        address = urljoin(self.site_url + "/", value)
        parsed = urlsplit(address)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        if parsed.username or parsed.password:
            return ""
        return address if re.search(r"\.(?:m3u8|mp4)$", parsed.path, re.I) else ""

    def _episodes(self, soup):
        """只读取正式选集元素，保留源站分集名称和出现顺序。"""
        episodes = []
        for node in soup.select(".episode-list > .episode[data-src]"):
            name = node.get_text(" ", strip=True)
            media = self._media_address(node.get("data-src", ""))
            if not name or any(character in name for character in ("$", "#")):
                raise RuntimeError("短剧大全源站分集名称无效，请更新源脚本")
            if not media:
                raise RuntimeError("短剧大全当前分集没有返回真实 HLS 或 MP4 地址")
            episodes.append({"name": name, "media": media})
        if not episodes:
            raise RuntimeError("短剧大全源站没有返回可识别的选集，请稍后重试或更新源脚本")
        return episodes

    def _episode_fingerprint(self, name):
        """记录源站集名指纹，播放重读后防止原集序变化导致播放错集。"""
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

    def _detail_page(self, item_id):
        """单部详情只接受数字 ID，404 明确表示原条目已下线。"""
        if not re.fullmatch(r"\d+", item_id):
            raise ValueError("短剧大全影片标识无效")
        return self._read_page(self.site_url + "/detail.php", {"vid": item_id}, allow_missing=True)

    def detailContent(self, ids):
        """打开详情时才取选集，播放标识保存影片 ID 和原集序号。"""
        if not ids:
            return {"list": []}
        item_id = str(ids[0])
        soup = self._detail_page(item_id)
        if soup is None:
            return {"list": []}
        title_node = soup.select_one(".video-title")
        title = title_node.get_text("", strip=True) if title_node else ""
        if not title:
            raise RuntimeError("短剧大全源站详情缺少标题，请更新源脚本")
        episodes = self._episodes(soup)
        video = soup.select_one("video[poster]")
        poster = video.get("poster", "") if video else ""
        # 关键变量：只保存原集序号和集名指纹，不落盘有时效的媒体 URL。
        playback_items = [episode["name"] + "$" + item_id + "-" + str(index) + "-"
                          + self._episode_fingerprint(episode["name"])
                          for index, episode in enumerate(episodes)]
        return {"list": [{
            "vod_id": item_id,
            "vod_name": title,
            "vod_pic": urljoin(self.site_url + "/", poster) if poster else "",
            # 源站 meta description 是全站 SEO 文案，不冒充本片剧情。
            "vod_content": "",
            "vod_remarks": str(len(episodes)) + "集",
            **self.category,
            "vod_play_from": "直链播放",
            "vod_play_url": "#".join(playback_items),
        }]}

    def playerContent(self, flag, id, vipFlags):
        """播放时刷新同部同集媒体地址，返回标准直链及必要请求头。"""
        match = re.fullmatch(r"(\d+)-(\d+)-([0-9a-f]{16})", str(id))
        if not match:
            raise ValueError("短剧大全分集播放标识无效")
        item_id, episode_index = match.group(1), int(match.group(2))
        soup = self._detail_page(item_id)
        if soup is None:
            raise RuntimeError("短剧大全源站中的影片已经下线")
        episodes = self._episodes(soup)
        if episode_index >= len(episodes):
            raise RuntimeError("短剧大全源站选集已经变化，请刷新影片详情")
        episode = episodes[episode_index]
        if self._episode_fingerprint(episode["name"]) != match.group(3):
            raise RuntimeError("短剧大全源站分集顺序已经变化，请刷新影片详情")
        address = episode["media"]
        return {"parse": 0, "url": address,
                "type": "hls" if urlsplit(address).path.lower().endswith(".m3u8") else "mp4",
                "header": {"User-Agent": self.headers["User-Agent"], "Referer": self.site_url + "/"}}
