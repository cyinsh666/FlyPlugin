# coding=utf-8
"""蚕驰短剧独立 Spider：分类、真实分页搜索及多线路取址兼容云助手。"""

import html
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    """仅请求蚕驰公开网页，不执行网页脚本或依赖其他源文件。"""

    display_name = "蚕驰短剧"

    def init(self, extend=""):
        """初始化独立会话参数，分类以源站首页的真实链接为准。"""
        self.site_url = "https://carbonnine.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.site_url + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        # 关键变量：分类名称只用来匹配源站的明确文字，不猜测影片所属类别。
        self.categories = []
        self.category_names = {}
        self.category_ids = {}

    def getName(self):
        """提供上传时可由宿主 AST 读取的固定名称。"""
        return "蚕驰短剧"

    def _read_page(self, address, params=None, allow_missing=False):
        """读取 UTF-8 网页；请求失败明确报告，避免缓存成空片库。"""
        response = self.fetch(address, params=params, headers=self.headers, timeout=15)
        if allow_missing and response.status_code == 404:
            return None
        response.raise_for_status()
        response.encoding = "utf-8"
        page_text = response.text
        if "系统安全验证" in page_text or "访问安全校验" in page_text:
            raise RuntimeError("蚕驰源站要求额外验证，请稍后重试")
        return BeautifulSoup(page_text, "html.parser")

    def _category_id(self, address):
        """从分类链接原始路径取 ID，分类分页不会改变类别身份。"""
        match = re.fullmatch(r"/ccdj/(\d+)(?:-\d+)?\.html", urlsplit(address).path)
        return match.group(1) if match else ""

    def _load_categories(self):
        """读取导航中的实际分类，搜索首次调用时也能独立得到映射。"""
        if self.categories:
            return
        soup = self._read_page(self.site_url + "/")
        categories = []
        seen = set()
        for link in soup.select(".myui-header__menu a[href], .nav-list a[href]"):
            category_id = self._category_id(link.get("href", ""))
            category_name = link.get_text(" ", strip=True)
            if not category_id or not category_name or category_id in seen:
                continue
            seen.add(category_id)
            categories.append({"type_id": category_id, "type_name": category_name})
        if not categories:
            raise RuntimeError("蚕驰源站没有返回可识别的分类")
        self.categories = categories
        self.category_names = {item["type_id"]: item["type_name"] for item in categories}
        self.category_ids = {item["type_name"]: item["type_id"] for item in categories}

    def _field_text(self, container, label):
        """读取标注字段后面的文本，到下一字段标注为止。"""
        if container is None:
            return ""
        for marker in container.select("span.text-muted"):
            if marker.get_text(strip=True).rstrip("：:") != label:
                continue
            parts = []
            for sibling in marker.next_siblings:
                if getattr(sibling, "name", None) == "span":
                    classes = sibling.get("class", [])
                    if "text-muted" in classes or "split-line" in classes:
                        break
                text = sibling.get_text(" ", strip=True) if getattr(sibling, "name", None) else str(sibling).strip()
                if text:
                    parts.append(text)
            return " ".join(parts).strip()
        return ""

    def _poster(self, link):
        """读取懒加载海报，分类轮播的 CSS 背景也可明确解析。"""
        if link is None:
            return ""
        value = link.get("data-original", "")
        if not value:
            image = link.select_one("img")
            if image is not None:
                value = image.get("data-original") or image.get("src", "")
        if not value:
            match = re.search(r"url\(\s*['\"]?([^)'\"]+)", link.get("style", ""))
            value = match.group(1).strip() if match else ""
        return urljoin(self.site_url + "/", value) if value else ""

    def _cards(self, soup, category_id=""):
        """仅解析正式结果列表，排除每页重复的推荐轮播和侧栏。"""
        selector = "ul.myui-vodlist > li" if category_id else "ul.myui-vodlist__media > li"
        cards = []
        seen = set()
        for row in soup.select(selector):
            link = row.select_one("a.myui-vodlist__thumb[href]")
            if link is None:
                continue
            match = re.fullmatch(r"/canchiduanju/(\d+)\.html", urlsplit(link["href"]).path)
            if not match or match.group(1) in seen:
                continue
            item_id = match.group(1)
            seen.add(item_id)
            # 关键变量：搜索页逐条取“分类：”，不把所有结果挂到搜索入口类别。
            actual_category = category_id
            if not actual_category:
                detail = row.select_one(".detail")
                actual_category = self.category_ids.get(self._field_text(detail, "分类"), "")
            if actual_category not in self.category_names:
                raise RuntimeError("蚕驰源站搜索结果缺少明确分类，请更新源脚本")
            title = link.get("title", "").strip()
            if not title:
                title_node = row.select_one("h4.title")
                title = title_node.get_text(" ", strip=True) if title_node else ""
            if not title:
                continue
            remark_node = link.select_one(".pic-text")
            cards.append({
                "vod_id": item_id,
                "vod_name": title,
                "vod_pic": self._poster(link),
                "vod_remarks": remark_node.get_text(" ", strip=True) if remark_node else "",
                "type_id": actual_category,
                "type_name": self.category_names[actual_category],
            })
        return cards

    def _page_number(self, value):
        """分页只接受明确的正整数。"""
        page = int(value or 1)
        if page < 1:
            raise ValueError("分页页码必须为正整数")
        return page

    def _pagination(self, soup, page, category_id=""):
        """只把源站尾页链接当总页数，不把页码窗口最大值误认为末页。"""
        pager = soup.select_one("ul.myui-page")
        if pager is None:
            return {}
        for link in pager.select("a[href]"):
            if link.get_text(strip=True) not in ("尾页", "最后一页"):
                continue
            address = html.unescape(link["href"])
            if category_id:
                match = re.fullmatch(r"/ccdj/" + re.escape(category_id) + r"-(\d+)\.html", urlsplit(address).path)
                last_page = int(match.group(1)) if match else 1 if self._category_id(address) == category_id else 0
            else:
                values = parse_qs(urlsplit(address).query).get("page", [])
                last_page = int(values[0]) if values and values[0].isdigit() else 0
            if last_page > 0:
                return {"pagecount": max(page, last_page)}
        # 未声明尾页时保留未知，由宿主继续取源页直至空页。
        return {}

    def homeContent(self, filter):
        """返回真实分类；首页混合推荐不冒充已知类别的扫描结果。"""
        self._load_categories()
        return {"class": self.categories, "list": []}

    def categoryContent(self, tid, pg, filter, extend):
        """读取正式目录卡片，分集和线路留到打开详情时再读取。"""
        self._load_categories()
        category_id = str(tid)
        if category_id not in self.category_names:
            raise ValueError("蚕驰分类不存在")
        page = self._page_number(pg)
        suffix = "" if page == 1 else "-" + str(page)
        soup = self._read_page(self.site_url + "/ccdj/" + category_id + suffix + ".html")
        cards = self._cards(soup, category_id)
        result = {"list": cards, "page": page, "limit": 48}
        result.update(self._pagination(soup, page, category_id))
        return result

    def searchContent(self, key, quick, pg="1"):
        """独立检索真实源页并直接返回卡片，请求参数保留原关键词和页码。"""
        self._load_categories()
        page = self._page_number(pg)
        soup = self._read_page(self.site_url + "/search.php", {"searchword": str(key), "page": page})
        cards = self._cards(soup)
        result = {"list": cards, "page": page, "limit": 10}
        result.update(self._pagination(soup, page))
        return result

    def _play_groups(self, soup, item_id):
        """保留源站线路名，按线路序号与原集序号构造稳定播放标识。"""
        names = {}
        for tab in soup.select('a[data-toggle="tab"][href^="#playlist"]'):
            names[tab["href"][1:]] = tab.get_text(" ", strip=True)
        groups = {}
        for container in soup.select('div[id^="playlist"]'):
            for link in container.select("a[href]"):
                match = re.fullmatch(r"/play/(\d+)-(\d+)-(\d+)\.html", urlsplit(link["href"]).path)
                if not match or match.group(1) != item_id:
                    continue
                line_id, episode_index = match.group(2), int(match.group(3))
                group = groups.setdefault(line_id, {"name": names.get(container.get("id"), ""), "episodes": {}})
                # 集数以源站播放路径的原序号为准，避免显示文案变化导致重复身份。
                group["episodes"][episode_index] = "第" + str(episode_index + 1) + "集$" + item_id + "-" + line_id + "-" + str(episode_index)
        flags = []
        urls = []
        used_names = set()
        for line_id in sorted(groups, key=int):
            group = groups[line_id]
            line_name = group["name"] or "线路" + str(int(line_id) + 1)
            if line_name in used_names:
                line_name += "（线路" + str(int(line_id) + 1) + "）"
            used_names.add(line_name)
            flags.append(line_name)
            urls.append("#".join(group["episodes"][index] for index in sorted(group["episodes"])))
        return "$$$".join(flags), "$$$".join(urls)

    def detailContent(self, ids):
        """详情直接读取真实分类和线路，宿主重启或首次搜索无需目录缓存。"""
        self._load_categories()
        if not ids:
            return {"list": []}
        item = self._detail_item(str(ids[0]))
        return {"list": [item] if item is not None else []}

    def _detail_item(self, item_id):
        """按需读取单片详情，404 明确视为条目下线。"""
        if not re.fullmatch(r"\d+", item_id):
            raise ValueError("蚕驰影片标识无效")
        soup = self._read_page(self.site_url + "/canchiduanju/" + item_id + ".html", allow_missing=True)
        if soup is None:
            return None
        detail = soup.select_one(".myui-content__detail")
        if detail is None:
            raise RuntimeError("蚕驰源站详情结构已变化，请更新源脚本")
        category_link = detail.select_one('a[href*="/ccdj/"]')
        category_id = self._category_id(category_link.get("href", "")) if category_link else ""
        if category_id not in self.category_names:
            raise RuntimeError("蚕驰源站详情缺少明确分类，请更新源脚本")
        title_node = detail.select_one("h1.title")
        if title_node is None:
            raise RuntimeError("蚕驰源站详情缺少标题，请更新源脚本")
        # 标题中的更新状态放入备注，不并入节目标题。
        status_node = title_node.find("font")
        remark = status_node.get_text(" ", strip=True) if status_node else ""
        if status_node:
            status_node.decompose()
        overview_node = soup.select_one("#jq .content, .myui-content__text")
        if overview_node is None:
            overview_node = detail.select_one(".desc .sketch")
        play_from, play_url = self._play_groups(soup, item_id)
        poster = soup.select_one('.myui-content__thumb a.myui-vodlist__thumb')
        return {
            "vod_id": item_id,
            "vod_name": title_node.get_text(" ", strip=True),
            "vod_pic": self._poster(poster),
            "vod_remarks": remark,
            "vod_content": overview_node.get_text(" ", strip=True) if overview_node else "",
            "type_id": category_id,
            "type_name": self.category_names[category_id],
            "vod_actor": self._field_text(detail, "主演"),
            "vod_director": self._field_text(detail, "导演"),
            "vod_area": self._field_text(detail, "地区"),
            "vod_year": self._field_text(detail, "年份"),
            "vod_play_from": play_from,
            "vod_play_url": play_url,
        }

    def _media_address(self, value):
        """只接受明确 HLS 或 MP4 地址，网页入口不能冒充可播放媒体。"""
        value = html.unescape(value).replace("\\/", "/").strip()
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        if parsed.username or parsed.password:
            return ""
        if not re.search(r"\.(?:m3u8|mp4)$", parsed.path, re.I):
            return ""
        return value

    def playerContent(self, flag, id, vipFlags):
        """播放时重新读取所选线路的同一集，仅返回真实直连媒体及必要头。"""
        playback_id = str(id)
        if not re.fullmatch(r"\d+-\d+-\d+", playback_id):
            raise ValueError("蚕驰分集播放标识无效")
        play_page = self.site_url + "/play/" + playback_id + ".html"
        soup = self._read_page(play_page)
        address = ""
        for script in soup.find_all("script"):
            script_text = script.string or script.get_text()
            # 关键变量：只读取字符串字面量，永不执行网页中的 JavaScript。
            match = re.search(r'\bvar\s+now\s*=\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', script_text)
            if match:
                literal = match.group(1)
                if literal.startswith('"'):
                    try:
                        candidate = json.loads(literal)
                    except (ValueError, TypeError):
                        candidate = ""
                else:
                    candidate = literal[1:-1].replace("\\'", "'")
                address = self._media_address(candidate)
                if address:
                    break
        if not address:
            raise RuntimeError("蚕驰当前线路没有返回可播放媒体，请尝试其他线路")
        container = "hls" if urlsplit(address).path.lower().endswith(".m3u8") else "mp4"
        return {"parse": 0, "url": address, "type": container, "header": {
            "User-Agent": self.headers["User-Agent"],
            "Referer": play_page,
        }}
