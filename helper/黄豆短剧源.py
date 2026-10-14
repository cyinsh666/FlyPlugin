# -*- coding: utf-8 -*-
"""黄豆独立 Spider：真实分类、摘要目录、按需分集与源站授权播放。"""

import gzip
import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
from urllib.parse import urlsplit

from base.spider import Spider as BaseSpider


SITE_URL = "https://lzlukvca.cc"
PAGE_SIZE = 18
# 关键变量：这些 cat_id 和名称均来自源站 navFilter 与对应目录，不是本地顺序编号。
CATEGORY_DEFINITIONS = (
    ("246506", "黄豆原创", "yuandou"),
    ("246502", "大神精选", "mod"),
    ("246503", "单体作品", "mod"),
    ("900011", "影视魔改", "mod"),
    ("5822103", "国漫AI", "mod"),
    ("2345505", "魔改短剧", "mod"),
    ("246505", "擦边短剧", "caibian"),
    ("817202", "都市", "zhenren"),
    ("900006", "古装", "zhenren"),
    ("1050901", "民国", "zhenren"),
    ("900007", "重生", "zhenren"),
    ("900008", "异能", "zhenren"),
    ("817201", "里番", "erciyuan"),
    ("4324502", "国漫", "erciyuan"),
    ("1050906", "日漫", "erciyuan"),
    ("481301", "动画同人", "erciyuan"),
    ("900002", "游戏同人", "erciyuan"),
    ("6655003", "美剧", "aiman"),
    ("6655009", "韩剧", "aiman"),
    ("1050902", "漫剧", "aiman"),
    ("1050905", "贤者", "zongyi"),
)


class _AESCBC:
    """兼容 NAS 的 Crypto 别名及本机已有 cryptography，均不安装依赖。"""

    @staticmethod
    def _crypt(data, key, iv, encrypt):
        """按源站 AES-CBC 协议处理字节，不把解密失败当成空响应。"""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            worker = cipher.encryptor() if encrypt else cipher.decryptor()
            return worker.update(data) + worker.finalize()
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return cipher.encrypt(data) if encrypt else cipher.decrypt(data)

    @staticmethod
    def encrypt(data, key, iv):
        """严格使用 PKCS7 补齐请求明文。"""
        padding_size = 16 - len(data) % 16
        padded = data + bytes([padding_size]) * padding_size
        return _AESCBC._crypt(padded, key, iv, True)

    @staticmethod
    def decrypt(data, key, iv):
        """校验响应的 PKCS7 补齐，错误密文不会继续被解析。"""
        plain = _AESCBC._crypt(data, key, iv, False)
        if not plain:
            raise ValueError("黄豆响应明文为空")
        padding_size = plain[-1]
        if not 1 <= padding_size <= 16 or plain[-padding_size:] != bytes([padding_size]) * padding_size:
            raise ValueError("黄豆响应补齐无效")
        return plain[:-padding_size]


class Spider(BaseSpider):
    """只访问固定原站；目录和搜索不调用详情或播放接口。"""

    display_name = "黄豆短剧"

    def init(self, extend=""):
        """保留每实例的匿名会话，重复初始化不丢失分类与详情缓存。"""
        if hasattr(self, "session_id"):
            return ""
        self.host = SITE_URL
        self.api = self.host + "/api"
        self.session_id = uuid.uuid4().hex
        self.device_id = self.session_id
        self.token = ""
        # 关键变量：原站公开 Web 协议的请求密钥，不是用户登录凭据。
        self.platform_key = "7961beb44246e3012ce228d6b5ced05a"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Origin": self.host,
            "Referer": self.host + "/home",
            "Content-Type": "application/octet-stream",
        }
        self.categories = []
        self.category_names = {}
        self.category_ids = {}
        self.category_parents = {}
        self.published_category_ids = set()
        self.detail_cache = {}
        if extend:
            try:
                config = json.loads(extend) if isinstance(extend, str) else extend
            except ValueError:
                raise ValueError("黄豆扩展配置必须是 JSON 对象") from None
            if not isinstance(config, dict):
                raise ValueError("黄豆扩展配置必须是 JSON 对象")
            # 用户可提供自己已有的原站授权；不替换原站地址或伪造购买状态。
            self.token = str(config.get("token") or "")
        return ""

    def getName(self):
        """提供上传时可由宿主 AST 识别的固定插件名称。"""
        return "黄豆短剧"

    def _key(self, request_id):
        """按原站请求 ID 派生当次 AES 密钥。"""
        request_bytes = bytes.fromhex(request_id.replace("-", ""))
        return hmac.new(self.platform_key.encode("utf-8"), request_bytes, hashlib.sha256).digest()

    def _decode(self, body, request_id):
        """兼容明文错误响应与原站 AES/gzip 正常响应。"""
        if body.lstrip().startswith((b"{", b"[")):
            try:
                return json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeError):
                # 密文 IV 偶尔也会以 JSON 起始字节开头，合法密文仍按 AES 协议读取。
                pass
        if len(body) < 32 or (len(body) - 16) % 16:
            raise ValueError("黄豆接口响应长度无效")
        plain = _AESCBC.decrypt(body[16:], self._key(request_id), body[:16])
        if plain.startswith(b"\x1f\x8b"):
            plain = gzip.decompress(plain)
        return json.loads(plain.decode("utf-8"))

    def _api(self, path, data):
        """只读调用审核过的原站接口，网络和业务失败均明确抛出。"""
        self.init()
        if path not in ("/drama/navList", "/drama/list", "/drama/detail", "/drama/play"):
            raise ValueError("黄豆接口路径无效")
        for attempt in range(2):
            # 只读请求仅重试一次，每次使用全新的请求 ID、IV 与签名。
            request_id = str(uuid.uuid4())
            iv = os.urandom(16)
            request = {"token": self.token, "deviceId": self.device_id, "data": data}
            plain = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            body = iv + _AESCBC.encrypt(gzip.compress(plain), self._key(request_id), iv)
            timestamp = str(int(time.time()))
            sign_input = "Dart|%s|%s|%s|%s" % (self.session_id, request_id, timestamp, path)
            headers = dict(self.headers)
            headers.update({
                "version": "2.0.0", "deviceType": "web", "time": timestamp,
                "sign": hashlib.sha256(sign_input.encode("utf-8")).hexdigest() + "-" + timestamp,
                "requestId": request_id, "sessionId": self.session_id,
                "deviceBrand": "", "deviceModel": "", "systemName": "", "systemVersion": "",
            })
            try:
                response = self.post(self.api + path, data=body, headers=headers, timeout=15)
            except Exception:
                if attempt == 0:
                    continue
                raise RuntimeError("黄豆源站网络请求失败，请稍后重试") from None
            if response.status_code >= 500 and attempt == 0:
                continue
            break
        if response.status_code != 200:
            raise RuntimeError("黄豆接口请求失败，HTTP %s" % response.status_code)
        try:
            result = self._decode(response.content, request_id)
        except (ValueError, UnicodeError, OSError):
            raise RuntimeError("黄豆接口没有返回有效数据") from None
        if not isinstance(result, dict):
            raise RuntimeError("黄豆接口响应结构无效")
        if result.get("status") != "y":
            raise RuntimeError("黄豆源站拒绝此请求，请检查源站授权或稍后重试")
        data = result.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("黄豆接口数据结构无效")
        return data

    def _load_categories(self):
        """首页只读取导航一次，使用已核对的真实叶分类省去八轮筛选请求。"""
        self.init()
        if self.categories:
            return
        data = self._api("/drama/navList", {})
        records = self._records(data)
        parents = {}
        for record in records:
            code = str(record.get("code") or "")
            name = str(record.get("name") or "").strip()
            if code and name:
                parents[code] = name
        categories = []
        used_parents = set()
        for category_id, category_name, parent_id in CATEGORY_DEFINITIONS:
            if parent_id not in parents:
                continue
            if parent_id not in used_parents:
                categories.append({"type_id": parent_id, "type_name": parents[parent_id]})
                used_parents.add(parent_id)
            categories.append({"type_id": category_id, "type_name": category_name, "type_pid": parent_id})
            self.category_names[category_id] = category_name
            self.category_ids[category_name] = category_id
            self.category_parents[category_id] = parent_id
            self.published_category_ids.add(category_id)
        if not self.category_names:
            raise RuntimeError("黄豆源站分类发生变化，请更新源脚本")
        # 源搜索仍可能返回未发布导航，保留真实类别让宿主筛除，不把这一页伪装为尾页。
        self.category_names["900012"] = "黑料"
        self.category_ids["黑料"] = "900012"
        self.category_parents["900012"] = "heiliao"
        self.categories = categories

    def _records(self, data):
        """只接受原站明确声明的列表，结构变化不能伪装成空页。"""
        records = data.get("list")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise RuntimeError("黄豆源站列表结构发生变化，请更新源脚本")
        return records

    def _page_number(self, value):
        """限制为真实正整数页码。"""
        text = str(value or "1")
        if not text.isdigit() or int(text) < 1:
            raise ValueError("黄豆页码必须是正整数")
        return int(text)

    def _item_id(self, item):
        """保留原站 ID，只去掉源站明确的 rp_ 命名空间前缀。"""
        item_id = str(item.get("id") or item.get("drama_id") or "")
        if item_id.startswith("rp_"):
            item_id = item_id[3:]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id):
            raise RuntimeError("黄豆源站返回了无效影片标识")
        return item_id

    def _category_id(self, item, hint=""):
        """真实分类优先；未发布类别只取得内部身份，不能进入目录或播放。"""
        name = str(item.get("category") or "").strip()
        for field in ("cat_id", "category_id", "categories"):
            value = item.get(field)
            if not isinstance(value, (str, int)) or not str(value).isdigit() or int(value) < 1:
                continue
            category_id = str(int(value))
            if category_id not in self.category_names:
                self.category_names[category_id] = name or category_id
                self.category_parents[category_id] = ""
            return category_id
        if name:
            known_id = self.category_ids.get(name)
            if known_id:
                return known_id
            # 原站搜索可能返回导航以外的类别：内部标识只防止过滤后被误认为空尾页。
            category_id = "unlisted:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            self.category_names[category_id] = name
            self.category_parents[category_id] = ""
            return category_id
        return hint if hint in self.published_category_ids else ""

    def _card(self, item, hint=""):
        """只复制源摘要，不填充分集或播放字段，不为未知搜索分类补详情。"""
        category_id = self._category_id(item, hint)
        if not category_id:
            raise RuntimeError("黄豆源站返回了未知分类，请更新源脚本")
        item_id = self._item_id(item)
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            raise RuntimeError("黄豆源站摘要缺少影片名称")
        count = item.get("episode_count")
        remarks = item.get("update_label") or item.get("corner") or ("全%s集" % count if count else "")
        return {
            "vod_id": item_id, "vod_name": title,
            "vod_pic": item.get("img_y") or item.get("img_x") or item.get("cover") or "",
            "vod_remarks": str(remarks), "type_id": category_id,
            "type_name": self.category_names[category_id],
            "type_id_1": self.category_parents[category_id],
        }

    def _page_result(self, data, page, category_id=""):
        """只采用原站总数或继续标记；没有声明时交给宿主按空页结束。"""
        cards = []
        for item in self._records(data):
            card = self._card(item, category_id)
            cards.append(card)
        result = {"list": cards, "page": page, "limit": PAGE_SIZE}
        total = data.get("total")
        if isinstance(total, (str, int)) and str(total).isdigit():
            result["total"] = int(total)
            result["pagecount"] = max(1, math.ceil(int(total) / PAGE_SIZE))
        elif data.get("hasmore") is False or data.get("has_more") is False:
            result["pagecount"] = page
        return result

    def homeContent(self, filter):
        """只返回分类，不加载混合推荐、全分类目录或详情。"""
        self._load_categories()
        return {"class": self.categories, "list": []}

    def categoryContent(self, tid, pg, filter, extend):
        """按用户选中的真实 cat_id 读取一页摘要。"""
        self._load_categories()
        category_id = str(tid)
        if category_id not in self.published_category_ids:
            raise ValueError("黄豆分类不存在，请选择具体子分类")
        page = self._page_number(pg)
        data = self._api("/drama/list", {
            "page": str(page), "page_size": str(PAGE_SIZE),
            "cat_id": category_id, "order": "new:top",
        })
        return self._page_result(data, page, category_id)

    def searchContent(self, key, quick, pg="1"):
        """源站实时搜索仅返回摘要，源站未识别分类不会挂到任意媒体库。"""
        self._load_categories()
        page = self._page_number(pg)
        data = self._api("/drama/list", {
            "page": str(page), "page_size": str(PAGE_SIZE), "keywords": str(key),
        })
        return self._page_result(data, page)

    def _detail(self, item_id):
        """详情只在详情或播放入口读取，短期缓存保留原始授权状态。"""
        self._load_categories()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id):
            raise ValueError("黄豆影片标识无效")
        cached = self.detail_cache.get(item_id)
        if cached is not None and time.monotonic() - cached[0] < 300:
            return cached[1]
        data = self._api("/drama/detail", {"id": item_id})
        if self._item_id(data) != item_id:
            raise RuntimeError("黄豆源站详情标识不一致")
        if not isinstance(data.get("episodes"), list):
            raise RuntimeError("黄豆源站没有返回真实分集")
        if self._category_id(data) not in self.published_category_ids:
            raise RuntimeError("黄豆详情所属分类未接入")
        self.detail_cache[item_id] = (time.monotonic(), data)
        # 独立进程只保留少量原始详情，避免浏览时间变长后无限积累。
        while len(self.detail_cache) > 64:
            self.detail_cache.pop(next(iter(self.detail_cache)))
        return data

    def _episodes(self, detail):
        """按源站原 seq 建立稳定集号，绝不按照总集数编造缺失分集。"""
        episodes = {}
        for episode in detail["episodes"]:
            if not isinstance(episode, dict):
                raise RuntimeError("黄豆分集结构无效")
            raw_seq = str(episode.get("seq") or "")
            if not raw_seq.isdigit() or int(raw_seq) < 1:
                raise RuntimeError("黄豆源站分集标识无效或重复")
            # 原始数字集号是身份依据，01 与 1 不能被当作两集。
            seq = str(int(raw_seq))
            if seq in episodes:
                raise RuntimeError("黄豆源站分集标识无效或重复")
            episodes[seq] = episode
        return episodes

    def detailContent(self, ids):
        """读取真实简介与选集；源站收费字段不被修改。"""
        if not ids:
            return {"list": []}
        item_id = str(ids[0])
        data = self._detail(item_id)
        card = self._card(data)
        if card is None:
            raise RuntimeError("黄豆详情分类尚未接入，请更新源脚本")
        episodes = self._episodes(data)
        entries = []
        for seq in sorted(episodes, key=int):
            # 选集名称由真实 seq 得到，剧情标题里的 #/$ 不会参与定界与身份。
            name = "第%s集" % seq
            entries.append(name + "$" + item_id + "|" + seq)
        card.update({
            "vod_content": str(data.get("description") or data.get("summary") or ""),
            "vod_play_from": self.display_name if entries else "",
            "vod_play_url": "#".join(entries),
        })
        return {"list": [card]}

    def _authorized(self, detail, episode):
        """只认可源站实际授权字段，会员与付费集不会伪装为免费集。"""
        if episode.get("is_buy") is True or detail.get("is_buy_whole") is True:
            return True
        if episode.get("type") == "free":
            return True
        return episode.get("type") == "vip" and detail.get("can_vip_watch") is True

    def playerContent(self, flag, id, vipFlags):
        """只返回原站为实际分集授权的地址，不拼 free 线路或预览地址。"""
        parts = str(id).split("|")
        if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) < 1:
            raise ValueError("黄豆播放标识无效")
        item_id = parts[0]
        seq = str(int(parts[1]))
        detail = self._detail(item_id)
        episode = self._episodes(detail).get(seq)
        if episode is None:
            raise RuntimeError("黄豆源站没有此分集")
        if not self._authorized(detail, episode):
            raise RuntimeError("此集需要黄豆源站的会员或购买授权")
        data = self._api("/drama/play", {"id": item_id, "seq": seq})
        address = data.get("m3u8") or data.get("url")
        if not isinstance(address, str) or urlsplit(address).scheme not in ("http", "https"):
            raise RuntimeError("黄豆源站未返回可播放地址，请检查原站授权")
        return {
            "parse": 0, "playUrl": "", "url": address, "jx": 0,
            "header": {
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/home", "Origin": self.host,
            },
        }
