# -*- coding: utf-8 -*-
"""独播库独立 Python Spider；列表只读摘要，详情和播放分别按需请求。"""

import base64
import random
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from base.spider import Spider as BaseSpider


API_URL = "https://api.dbokutv.com"
SITE_URL = "https://www.dbku.tv"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# 关键变量：分类 ID 来自原站 vodshow 接口，不使用首页混合推荐推断分类。
CATEGORIES = (("1", "电影"), ("2", "连续剧"), ("3", "综艺"), ("4", "动漫"))
CATEGORY_NAMES = dict(CATEGORIES)
# 关键变量：只映射源站明确展示的类型名，不根据片名、更新集数或演员推断。
SEARCH_CATEGORY_IDS = {
    "电影": "1", "连续剧": "2", "综艺": "3", "动漫": "4",
    "陆剧": "2", "港剧": "2", "日韩剧": "2", "台泰剧": "2", "短剧": "2",
}


class _HtmlNode:
    """保存搜索页的局部 HTML 结构，标准库即可解析每张卡片的明确分类。"""

    def __init__(self, tag, attrs=None):
        """保留标签、属性和原始子节点，避免跨卡片提取导航类别。"""
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []

    def text(self):
        """读取当前节点内部文本，不加入页面其他卡片的内容。"""
        return "".join(child.text() if isinstance(child, _HtmlNode) else child for child in self.children)

    def nodes(self, tag=None, class_name=None):
        """按标签和单个 class 查找后代节点，保持页面原始顺序。"""
        found = []
        for child in self.children:
            if not isinstance(child, _HtmlNode):
                continue
            if (tag is None or child.tag == tag) and (
                class_name is None or class_name in str(child.attrs.get("class") or "").split()
            ):
                found.append(child)
            found.extend(child.nodes(tag, class_name))
        return found

    def first(self, tag=None, class_name=None):
        """获取局部结构中的第一个匹配节点，不匹配时明确返回空。"""
        found = self.nodes(tag, class_name)
        return found[0] if found else None


class _SearchDocument(HTMLParser):
    """只构建静态 HTML 结构，不运行页面脚本或加载页面中的资源。"""

    def __init__(self):
        """初始化标签栈，HTML 单标签不会吞并后续卡片。"""
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document")
        self.stack = [self.root]
        self.void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def handle_starttag(self, tag, attrs):
        """将标签加入当前父节点，普通标签进入栈等待结束。"""
        node = _HtmlNode(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.void_tags:
            self.stack.append(node)

    def handle_endtag(self, tag):
        """关闭最近的同名标签，容忍原页面省略的结束标签。"""
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        """保留文本以读取分类标记后、分隔线前的原始类型名称。"""
        self.stack[-1].children.append(data)


class Spider(BaseSpider):
    """通过原站公开接口获取内容，不依赖第三方 HTML 库或用户账号。"""

    display_name = "独播库"

    def init(self, extend=""):
        """初始化不联网、不扫描分类，也不预先缓存详情或临时播放地址。"""
        return ""

    def getName(self):
        """返回宿主可静态识别的固定名称。"""
        return "独播库"

    def _request(self, route, params=None):
        """检查 HTTP 与 JSON 响应，源站失败不伪装为没有影片。"""
        try:
            response = self.fetch(
                self.sign(API_URL + route),
                params=params,
                headers={
                    "User-Agent": USER_AGENT,
                    "Connection": "Keep-Alive",
                    "Accept-Encoding": "gzip",
                    "Referer": SITE_URL + "/",
                },
                timeout=15,
            )
        except Exception:
            raise RuntimeError("独播库源站网络请求失败，请稍后重试") from None
        if response.status_code != 200:
            raise RuntimeError("独播库接口请求失败，HTTP %s" % response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError("独播库接口没有返回有效 JSON") from None
        if not isinstance(result, (dict, list)):
            raise RuntimeError("独播库接口响应结构无效")
        return result

    def decode(self, value):
        """按原站每十字符反转再 Base64 解码，不执行任何远程代码。"""
        if not isinstance(value, str) or not value:
            raise RuntimeError("独播库接口缺少编码字段")
        encoded = value.strip("'\"")
        # 关键变量：点号是原站的 Base64 补齐字符，十字符分段顺序不可改变。
        decoded_segments = [encoded[index:index + 10][::-1] for index in range(0, len(encoded), 10)]
        prepared = "".join(decoded_segments).replace(".", "=")
        prepared += "=" * ((-len(prepared)) % 4)
        try:
            return base64.b64decode(prepared, validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            raise RuntimeError("独播库接口编码字段无效") from None

    def interleave_strings(self, first, second):
        """保留原站签名要求的随机数组合与时间戳交错顺序。"""
        count = min(len(first), len(second))
        pairs = [first[index] + second[index] for index in range(count)]
        return "".join(pairs) + first[count:] + second[count:]

    def random_string(self, length, timestamp):
        """使用局部随机状态生成原站公开签名串，不污染其他源的随机状态。"""
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        generator = random.Random(timestamp + len(alphabet))
        return "".join(generator.choice(alphabet) for _ in range(length))

    def sign(self, raw):
        """沿用原站公开 Web 签名算法，不复用过期的带签名请求地址。"""
        timestamp = int(time.time())
        generator = random.Random(timestamp)
        number = generator.randint(0, 800000000)
        first = "%s%s" % (number + 100000000, 900000000 - number)
        mixed = self.interleave_strings(first, str(timestamp))
        ssid = base64.b64encode(mixed.encode("utf-8")).decode("ascii").replace("=", ".")
        return "%s?sign=%s&token=%s&ssid=%s" % (
            raw, self.random_string(60, timestamp), self.random_string(38, timestamp), ssid
        )

    def _page_number(self, value):
        """只接受正整数页码，避免错误页码被原站静默当作第一页。"""
        text = str(value)
        if not text.isdigit() or int(text) < 1:
            raise ValueError("独播库页码必须是正整数")
        return int(text)

    def _source_path(self, value):
        """保留原站稳定相对路径，签名参数和临时媒体地址不参与影片身份。"""
        if not isinstance(value, str):
            raise RuntimeError("独播库内容标识无效")
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise RuntimeError("独播库内容标识不是源站相对路径")
        if not value.startswith("/") or value.startswith("//") or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
            raise RuntimeError("独播库内容标识路径无效")
        if ".." in value.split("/"):
            raise RuntimeError("独播库内容标识路径无效")
        return value

    def _identity(self, category_id, path):
        """影片身份同时携带真实分类，重启后访问详情也不依赖内存分类缓存。"""
        return "%s@@%s" % (category_id, self._source_path(path))

    def _read_identity(self, value):
        """从稳定身份读取分类和源站详情路径，拒绝没有来源分类的条目。"""
        parts = str(value).split("@@")
        if len(parts) != 2 or parts[0] not in CATEGORY_NAMES:
            raise ValueError("独播库影片标识无效")
        return parts[0], self._source_path(parts[1])

    def _summary(self, record, category_id):
        """目录和搜索只返回源站卡片，不额外读取简介、分集或播放地址。"""
        if not isinstance(record, dict):
            raise RuntimeError("独播库影片摘要结构无效")
        path = self.decode(record.get("DId") or record.get("DuId"))
        name = record.get("Name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("独播库影片摘要缺少标题")
        return {
            "vod_id": self._identity(category_id, path),
            "type_id": category_id,
            "type_name": CATEGORY_NAMES[category_id],
            "vod_name": name.strip(),
            "vod_pic": self.decode(record["TnId"]) if record.get("TnId") else "",
            "vod_remarks": record.get("Tag") or "",
        }

    def _page_count(self, pagination):
        """仅从原站末页链接或总页数标记读取分页，不制造精确总数。"""
        if not isinstance(pagination, list):
            return None
        pages = []
        for entry in pagination:
            if not isinstance(entry, dict):
                continue
            if entry.get("Type") == "StartEnd" and (entry.get("PId") or entry.get("PuId")):
                path = self.decode(entry.get("PId") or entry.get("PuId"))
                segments = path.split("-")
                if len(segments) > 8:
                    text = segments[8]
                    if text.isdigit() and int(text) > 0:
                        pages.append(int(text))
            if entry.get("Type") == "ShortPage":
                match = re.fullmatch(r"\s*\d+\s*/\s*(\d+)\s*", str(entry.get("Name") or ""))
                if match and int(match.group(1)) > 0:
                    pages.append(int(match.group(1)))
        return max(pages) if pages else None

    def homeContent(self, filter=False):
        """首页只公布四个真实分类，首次同步由宿主逐分类读取整页摘要。"""
        return {"class": [
            {"type_id": category_id, "type_name": name}
            for category_id, name in CATEGORIES
        ]}

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        """将真实页码直接传入 vodshow，列表不调用任何详情或取流接口。"""
        category_id = str(tid)
        if category_id not in CATEGORY_NAMES:
            raise ValueError("独播库分类不存在")
        page = self._page_number(pg)
        # 关键变量：源站第一页在第九段留空，后续页保留实际页码。
        page_segment = "" if page == 1 else str(page)
        data = self._request("/vodshow/%s--------%s---" % (category_id, page_segment))
        if not isinstance(data, dict) or not isinstance(data.get("VodList"), list):
            raise RuntimeError("独播库分类接口缺少 VodList 列表")
        result = {
            "list": [self._summary(record, category_id) for record in data["VodList"]],
            "page": page,
        }
        page_count = self._page_count(data.get("PaginationList"))
        if page_count is not None:
            result["pagecount"] = page_count
        return result

    def _search_field(self, detail, label):
        """仅从当前卡片的明确字段标签后读值，到该字段分隔线为止。"""
        for paragraph in detail.nodes("p"):
            for index, child in enumerate(paragraph.children):
                if not isinstance(child, _HtmlNode) or child.tag != "span":
                    continue
                classes = str(child.attrs.get("class") or "").split()
                if "text-muted" not in classes or child.text().strip() != label + "：":
                    continue
                values = []
                for sibling in paragraph.children[index + 1:]:
                    if isinstance(sibling, _HtmlNode):
                        sibling_classes = str(sibling.attrs.get("class") or "").split()
                        if "split-line" in sibling_classes or "text-muted" in sibling_classes:
                            break
                        values.append(sibling.text())
                    else:
                        values.append(sibling)
                return "".join(values).strip()
        return ""

    def _search_document(self, keyword):
        """使用已核对的官方搜索入口，一次请求直接获取带真实分类的摘要。"""
        try:
            response = self.fetch(
                SITE_URL + "/vodsearch/-------------.html",
                params={"wd": keyword},
                headers={"User-Agent": USER_AGENT, "Referer": SITE_URL + "/"},
                timeout=15,
            )
        except Exception:
            raise RuntimeError("独播库官方搜索网络请求失败，请稍后重试") from None
        if response.status_code != 200:
            raise RuntimeError("独播库官方搜索请求失败，HTTP %s" % response.status_code)
        try:
            html = response.content.decode("utf-8-sig")
        except UnicodeError:
            raise RuntimeError("独播库官方搜索页面编码无效") from None
        parser = _SearchDocument()
        parser.feed(html)
        parser.close()
        title = parser.root.first("title")
        expected_title = keyword + "搜索结果 - 独播库"
        if title is None or title.text().strip() != expected_title:
            raise RuntimeError("独播库官方搜索未返回正确结果页，请稍后重试")
        expected_heading = re.sub(r"\s+", "", "与“%s”相关的搜索结果" % keyword)
        if expected_heading not in re.sub(r"\s+", "", parser.root.text()):
            raise RuntimeError("独播库官方搜索页面缺少结果标题")
        lists = parser.root.nodes("ul", "myui-vodlist__media")
        if not lists:
            raise RuntimeError("独播库官方搜索页面缺少结果列表")
        # 关键变量：有效空搜索也必须具有正确结果页标题及搜索结果容器。
        return lists

    def searchContent(self, key, quick=False):
        """官方 HTML 单页搜索保留真实分类，不逐条查询详情或重复返回第二页。"""
        keyword = str(key).strip()
        if not keyword:
            return {"list": [], "page": 1, "pagecount": 1}
        lists = self._search_document(keyword)
        entries = [
            child for container in lists for child in container.children
            if isinstance(child, _HtmlNode) and child.tag == "li"
        ]
        if len(entries) > 200:
            raise RuntimeError("独播库搜索结果超过 200 条，请缩小关键词范围")
        cards = []
        identities = set()  # 关键变量：相同源站 ID 与分类只保存一张卡片。
        for entry in entries:
            detail = entry.first("div", "detail")
            heading = detail.first("h4", "title") if detail else None
            link = heading.first("a") if heading else None
            if link is None or not link.text().strip():
                raise RuntimeError("独播库搜索卡片缺少标题或详情链接")
            href = urlsplit(urljoin(SITE_URL, str(link.attrs.get("href") or "")))
            match = re.fullmatch(r"/voddetail/(\d+)\.html", href.path)
            if href.netloc != urlsplit(SITE_URL).netloc or not match:
                raise RuntimeError("独播库搜索卡片的影片身份无效")
            category_name = self._search_field(detail, "分类")
            category_id = SEARCH_CATEGORY_IDS.get(category_name)
            if not category_id:
                raise RuntimeError("独播库搜索出现未支持的源站分类：%s" % (category_name or "缺失"))
            identity = self._identity(category_id, "/voddetail/" + match.group(1))
            if identity in identities:
                continue
            identities.add(identity)
            thumbnail = entry.first("a", "myui-vodlist__thumb")
            poster = str(thumbnail.attrs.get("data-original") or "") if thumbnail else ""
            remarks = thumbnail.first("span", "pic-text") if thumbnail else None
            score = thumbnail.first("span", "tag") if thumbnail else None
            score_match = re.fullmatch(r"(\d+(?:\.\d+)?)分", score.text().strip()) if score else None
            cards.append({
                "vod_id": identity,
                "type_id": category_id,
                "type_name": CATEGORY_NAMES[category_id],
                "vod_name": link.text().strip(),
                "vod_pic": urljoin(SITE_URL, poster) if poster else "",
                "vod_remarks": remarks.text().strip() if remarks else "",
                "vod_score": score_match.group(1) if score_match else "",
                "vod_year": self._search_field(detail, "年份"),
                "vod_area": self._search_field(detail, "地区"),
                "vod_actor": self._search_field(detail, "主演"),
                "vod_director": self._search_field(detail, "导演"),
            })
        return {"list": cards, "page": 1, "pagecount": 1}

    def _metadata_text(self, value):
        """演员等字段按源站实际列表或文本展示，不根据内容推断分类。"""
        if isinstance(value, list):
            return ",".join(str(item) for item in value if item is not None)
        return "" if value is None else str(value)

    def detailContent(self, ids):
        """打开详情或选集时才获取完整元数据和原站真实分集列表。"""
        identity = ids[0] if isinstance(ids, list) and ids else ids
        category_id, path = self._read_identity(identity)
        data = self._request(path)
        if not isinstance(data, dict) or not isinstance(data.get("Playlist"), list):
            raise RuntimeError("独播库详情接口缺少分集列表")
        entries = []
        names = set()  # 关键变量：同线路重复分集必须报错，避免播放到另一集。
        for episode in data["Playlist"]:
            if not isinstance(episode, dict):
                raise RuntimeError("独播库分集结构无效")
            name = re.sub(r"[#$\r\n]", " ", str(episode.get("EpisodeName") or "")).strip()
            if not name or name in names:
                raise RuntimeError("独播库分集名称缺失或重复")
            names.add(name)
            play_path = self._source_path(self.decode(episode.get("VId")))
            entries.append("%s$%s" % (name, play_path))
        if not isinstance(data.get("Name"), str) or not data["Name"].strip():
            raise RuntimeError("独播库详情缺少影片名称")
        return {"list": [{
            "vod_id": str(identity),
            "type_id": category_id,
            "type_name": CATEGORY_NAMES[category_id],
            "vod_name": data["Name"].strip(),
            "vod_pic": self.decode(data["TnId"]) if data.get("TnId") else "",
            "vod_remarks": "评分：%s" % data["Rating"] if data.get("Rating") else "",
            "vod_score": str(data.get("Rating") or ""),
            "vod_year": str(data.get("ReleaseYear") or ""),
            "vod_area": self._metadata_text(data.get("Region")),
            "vod_actor": self._metadata_text(data.get("Actor")),
            "vod_director": self._metadata_text(data.get("Director")),
            "vod_content": self._metadata_text(data.get("Description")),
            "vod_class": self._metadata_text(data.get("Scenario")),
            "vod_play_from": "独播库" if entries else "",
            "vod_play_url": "#".join(entries),
        }]}

    def playerContent(self, flag, id, vipFlags=None):
        """播放时重新请求所选分集的 HId，数据库仅保存稳定的分集取址路径。"""
        data = self._request(self._source_path(id))
        if not isinstance(data, dict):
            raise RuntimeError("独播库取流接口响应结构无效")
        media_url = self.decode(data.get("HId")).strip()
        parsed = urlsplit(media_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            raise RuntimeError("独播库没有返回有效媒体地址")
        result = {
            "parse": 0,
            "url": media_url,
            "header": {
                "User-Agent": USER_AGENT + " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept-Encoding": "gzip, deflate",
                "Origin": "https://w.duboku.io",
                "Referer": "https://w.duboku.io/",
                "priority": "u=1, i",
            },
        }
        if parsed.path.lower().endswith(".m3u8"):
            result["type"] = "hls"
        elif parsed.path.lower().endswith(".mp4"):
            result["type"] = "mp4"
        return result
