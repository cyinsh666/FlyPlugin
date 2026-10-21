# -*- coding: utf-8 -*-
"""
==================================================
@Spider Name : WanWuu Spider (Multi-Domain & Auto-Publish Release)
@Author      : 飞鱼
@Description : TVBox / CatVod 社区详情页真实地址提取 + 防盗链 Referer 播放修复版
              v7: 重构 vod_remarks，将播放时长作为主要副标题显示，分类/标签紧随其后作为角标
==================================================
"""
import json
import re
import time
from urllib.parse import quote, quote_plus, unquote, urljoin
from bs4 import BeautifulSoup
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):

    # 预设备用域名池
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = '玩物社区'

    DOMAINS = [
        "https://tju.bnmdquasi.cc",
        "https://xmu.ezgdtehh.com",
        "https://xmu.gpcqqmsof.cc",
        "https://thu.bnmdquasi.cc",
        "https://thu.gpcqqmsof.cc",
        "https://jlu.ezgdtehh.com",
        "https://hit.bnmdquasi.cc",
        "https://zju.gpcqqmsof.cc",
        "https://sysu.bnmdquasi.cc",
        "https://wanwuu.com"
    ]

    # 地址发布页列表
    PUBLISH_PAGES = [
        "https://wanwuu.pages.dev/",
        "https://wanwuu.github.io/",
    ]

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '玩物社区'

    def init(self, extend=""):
        """延迟联网到目录请求，导入与初始化不顺序探测整套域名池。"""
        config = extend if isinstance(extend, dict) else {}
        if isinstance(extend, str) and extend.strip():
            config = {"host": extend.strip()} if extend.strip().startswith(("http://", "https://")) else json.loads(extend)
        if not isinstance(config, dict):
            return self.sourceFailure("源扩展配置必须是 JSON 对象或地址")
        self.site_url = str(config.get("host") or self.DOMAINS[0]).rstrip("/")
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8", "Referer": self.site_url + "/"}
        if config.get("cookie"):
            self.headers["Cookie"] = str(config["cookie"])
        self.u_param_pattern = re.compile(r'encodeURIComponent\("([^\"]+)"\)')
        self.u_param_pattern_alt = re.compile(r'u=([a-zA-Z0-9%_\.-]+)')
        self.m3u8_pattern = re.compile(r'(https?://[^"\']+\.m3u8[^"\']*)', re.IGNORECASE)

    # ---------- 域名动态探测与切换核心 ----------

    def _get_working_domain(self):
        """有限时间探测源资料中的备用域名，全部失败明确报错。"""
        # 关键变量：域名发现限制在 25 秒内，避免目录请求长期占满 worker。
        deadline = time.monotonic() + 25
        candidates = list(self.DOMAINS)
        for domain in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 4:
                break
            try:
                response = self.fetch(domain.rstrip("/") + "/videos/new/", headers=self.headers, timeout=min(2, remaining))
                if response.status_code == 200 and len(response.text) > 500:
                    return domain.rstrip("/")
            except Exception:
                continue
        for publish in self.PUBLISH_PAGES:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            try:
                response = self.fetch(publish, headers=self.headers, timeout=min(3, remaining))
                if response.status_code != 200:
                    continue
                for address in re.findall(r'https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}', response.text):
                    if address in candidates or "github.io" in address or "pages.dev" in address:
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 1:
                        break
                    candidates.append(address)
                    candidate = self.fetch(address + "/videos/new/", headers=self.headers, timeout=min(2, remaining))
                    if candidate.status_code == 200 and len(candidate.text) > 500:
                        return address
            except Exception:
                continue
        return self.sourceFailure("源站备用域名均不可访问，请更新原站地址")

    def _test_connectivity(self, domain):
        """只读取公开目录确认备用域名是否响应。"""
        try:
            res = self.fetch(f"{domain}/videos/new/", headers=self.headers, timeout=5)
            if res and hasattr(res, 'status_code') and res.status_code == 200 and len(res.text) > 500:
                return True
            if res and isinstance(res, str) and len(res) > 500:
                return True
        except Exception:
            pass
        return False

    def _fetch_domains_from_publish_pages(self):
        """从原站公开发布页提取备用域名。"""
        extracted_domains = []
        for pub_url in self.PUBLISH_PAGES:
            try:
                res = self.fetch(pub_url, headers=self.headers, timeout=8)
                if not res:
                    continue

                html = res.text if hasattr(res, 'text') else str(res)
                if not html:
                    continue

                js_match = re.search(r'src=["\']?(publish\.js[^"\'\s>]*)"\']?', html)
                if js_match:
                    js_url = urljoin(pub_url, js_match.group(1))
                    js_res = self.fetch(js_url, headers=self.headers, timeout=8)
                    if js_res:
                        js_text = js_res.text if hasattr(js_res, 'text') else str(js_res)
                        html += "\n" + js_text

                found = re.findall(r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
                for d in found:
                    d_clean = d.rstrip("/")
                    if (
                        "github.io" not in d_clean
                        and "pages.dev" not in d_clean
                        and d_clean not in extracted_domains
                    ):
                        extracted_domains.append(d_clean)
            except Exception:
                continue
        return extracted_domains

    def _fetch_html_safe(self, path_or_url, allow_missing_page=False):
        """请求真实页面；站点切换后将同站旧地址换成新域名再尝试一次。"""
        value = str(path_or_url or "")
        original = self.site_url
        url = urljoin(original + "/", value)
        html = self._fetch_html(url)
        if html:
            return html
        # 关键变量：原站搜索越过末页会返回真实 404；只对后续分页视为结束。
        if allow_missing_page and self._last_fetch_status == 404:
            return ""
        self.site_url = self._get_working_domain()
        self.headers["Referer"] = self.site_url + "/"
        if value.startswith(original):
            value = value[len(original):]
        url = urljoin(self.site_url + "/", value)
        html = self._fetch_html(url)
        if not html:
            return self.sourceFailure("源站页面无法读取")
        return html

    def _fetch_html(self, url):
        """保存真实 HTTP 状态，后续页的 404 与网络故障分别处理。"""
        self._last_fetch_status = None
        try:
            res = self.fetch(url, headers=self.headers, timeout=8)
            self._last_fetch_status = getattr(res, "status_code", None)
            if not res:
                return ""
            if isinstance(res, str):
                return res
            return res.text if hasattr(res, 'text') else str(res)
        except Exception:
            return ""

    # ============================================================
    # 36进制转换（packed JS 标准算法）
    # ============================================================
    def _int_to_base36(self, num):
        """按照标准三十六进制编码脚本中的索引。"""
        if num == 0:
            return '0'
        chars = '0123456789abcdefghijklmnopqrstuvwxyz'
        result = ''
        while num > 0:
            result = chars[num % 36] + result
            num //= 36
        return result

    # ============================================================
    # JS字符串转义解析
    # ============================================================
    def _unescape_js_string(self, s):
        """还原脚本文本转义，不执行网页脚本。"""
        result = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                nxt = s[i + 1]
                if nxt == 'n':
                    result.append('\n')
                elif nxt == 'r':
                    result.append('\r')
                elif nxt == 't':
                    result.append('\t')
                elif nxt == '\\':
                    result.append('\\')
                elif nxt == '"':
                    result.append('"')
                elif nxt == "'":
                    result.append("'")
                elif nxt == '/':
                    result.append('/')
                elif nxt == 'b':
                    result.append('\b')
                elif nxt == 'f':
                    result.append('\f')
                elif nxt == 'x' and i + 3 < len(s):
                    try:
                        result.append(chr(int(s[i+2:i+4], 16)))
                        i += 2
                    except:
                        result.append(s[i:i+2])
                elif nxt == 'u' and i + 5 < len(s):
                    try:
                        result.append(chr(int(s[i+2:i+6], 16)))
                        i += 4
                    except:
                        result.append(s[i:i+2])
                else:
                    result.append(nxt)
                i += 2
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    # ============================================================
    # 通用 eval packed JS 解码器（Dean Edwards Packer）
    # ============================================================
    def _decode_packed_js(self, html):
        """静态展开播放器打包文本，不执行网页脚本。"""
        if not html:
            return None

        words = None
        split_match = None
        for pattern in [
            r"'([^']{20,})'\.split\('\|'\)",
            r'"([^"]{20,})"\.split\("\|"\)',
        ]:
            split_match = re.search(pattern, html)
            if split_match:
                words = split_match.group(1).split('|')
                if len(words) >= 5:
                    break

        if not words or len(words) < 5:
            return None

        packed_area = html[:split_match.start()]
        template = None

        for quote_char in ("'", '"'):
            marker = "}(" + quote_char
            pos = packed_area.rfind(marker)
            if pos == -1:
                continue

            i = pos + 3
            content = []
            while i < len(packed_area):
                ch = packed_area[i]
                if ch == '\\' and i + 1 < len(packed_area):
                    content.append(ch)
                    content.append(packed_area[i + 1])
                    i += 2
                elif ch == quote_char:
                    between = packed_area[i + 1:split_match.start()]
                    if re.match(r"\s*,\s*\d+\s*,\s*\d+\s*,\s*", between):
                        template = ''.join(content)
                        break
                    else:
                        content.append(ch)
                        i += 1
                else:
                    content.append(ch)
                    i += 1

            if template is not None:
                break

        if not template:
            return None

        template = self._unescape_js_string(template)

        for c in range(len(words) - 1, -1, -1):
            if words[c]:
                token = self._int_to_base36(c)
                template = re.sub(r'\b' + re.escape(token) + r'\b', words[c], template)

        return template

    def _extract_play_url(self, html, page_url=""):
        """从原站播放器文本读取真实媒体地址。"""
        if not html:
            return ""

        u_param = ""

        u_match = self.u_param_pattern.search(html)
        if u_match:
            u_param = u_match.group(1)
        else:
            u_alt = self.u_param_pattern_alt.search(html)
            if u_alt:
                u_param = unquote(u_alt.group(1))

        if not u_param:
            decoded_html = self._decode_packed_js(html)
            if decoded_html:
                u_match = self.u_param_pattern.search(decoded_html)
                if u_match:
                    u_param = u_match.group(1)
                else:
                    u_alt = self.u_param_pattern_alt.search(decoded_html)
                    if u_alt:
                        u_param = unquote(u_alt.group(1))

        if not u_param:
            b64_match = re.search(
                r'["\']([A-Za-z0-9_\-]{10,}/[A-Za-z0-9_\-]{5,}\+[A-Za-z0-9_\-]{5,}/[A-Za-z0-9_\-]{10,}\+[A-Za-z0-9_\-]{1,}=?)["\']',
                html
            )
            if b64_match:
                u_param = b64_match.group(1)

        if not u_param:
            return ""

        t_param = str(int(time.time() / 1800))

        api_url = (
            f"{self.site_url}/videos/melon_detail_play?"
            f"img=&u={quote(u_param, safe='')}&t={t_param}"
        )

        api_headers = dict(self.headers)
        if page_url:
            api_headers["Referer"] = (
                page_url if page_url.startswith("http") else f"{self.site_url}{page_url}"
            )

        try:
            res = self.fetch(api_url, headers=api_headers, timeout=10)
            if not res:
                return ""

            if isinstance(res, str):
                js_content = res
            else:
                if hasattr(res, 'status_code') and res.status_code != 200:
                    return ""
                js_content = res.text if hasattr(res, 'text') else str(res)

            search_content = js_content

            decoded_api = self._decode_packed_js(js_content)
            if decoded_api:
                search_content = decoded_api + "\n" + js_content
            else:
                eval_peel = re.search(r'eval\s*\(\s*function\(.*?\)\s*\{.*?return\s+["\'](.*?)["\']', js_content, re.DOTALL)
                if eval_peel:
                    search_content = eval_peel.group(1) + "\n" + js_content

            m3u8_match = self.m3u8_pattern.search(search_content)
            if m3u8_match:
                return m3u8_match.group(1).replace(r"\/", "/")

            mp4_match = re.search(r'(https?://[^"\']+\.mp4[^"\']*)', search_content, re.I)
            if mp4_match:
                return mp4_match.group(1).replace(r"\/", "/")

            json_match = re.search(r'["\']url["\']\s*:\s*["\']([^"\']+)["\']', search_content)
            if json_match:
                url = json_match.group(1).replace(r"\/", "/")
                if ".m3u8" in url or ".mp4" in url:
                    return url

            any_url = re.search(r'["\'](https?://[^"\']+)["\']', search_content)
            if any_url:
                candidate = any_url.group(1).replace(r"\/", "/")
                if "m3u8" in candidate or "mp4" in candidate or "video" in candidate:
                    return candidate

        except Exception:
            pass

        return ""

    # ---------- 业务核心方法 ----------

    def homeContent(self, filter=False):
        """影视插件只发布原站视频类别，不把小说、帖子和标签目录当影片。"""
        return {"class": [{"type_id": "discover_all", "type_name": "发现"},
                          {"type_id": "videos_zhibo", "type_name": "视频"},
                          {"type_id": "ai_all", "type_name": "AI短剧"},
                          {"type_id": "porn_all", "type_name": "成人视频"}]}

    def homeVideoContent(self):
        """读取原站首页的真实影片卡片。"""
        html = self._fetch_html_safe("/videos/popular/")
        videos = self._parse_video_list(html)
        return {"list": videos}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        """仅将视频卡片加入目录，分页不使用固定总量。"""
        paths = {"discover_all": "/videos/watchings/", "videos_zhibo": "/videos/new/",
                 "ai_all": "/ai/all/", "porn_all": "/porn/all/"}
        category_id = str(tid)
        if category_id not in paths:
            return self.sourceFailure("当前分类不是影视类别")
        page = max(1, int(pg or 1))
        path = paths[category_id].rstrip("/") + ("/page/%s/" % page if page > 1 else "/")
        items = self._parse_video_list(self._fetch_html_safe(path, allow_missing_page=page > 1))
        for item in items:
            item["type_id"] = category_id
        return self.pageResult(items, page)

    def detailContent(self, ids):
        """返回真实单视频页面与媒体地址，不输出小说或伪造集数。"""
        if not ids:
            return {"list": []}
        value = str(ids[0])
        if any(prefix in value for prefix in ("/novels/", "/posts/", "/tags/", "/moviesets/")):
            return self.sourceFailure("此条目不是独立视频")
        page_url = urljoin(self.site_url + "/", value)
        html = self._fetch_html_safe(page_url)
        soup = BeautifulSoup(html, "html.parser")
        title_node = soup.select_one("h1, title")
        title = title_node.get_text(strip=True) if title_node else ""
        if not title:
            return self.sourceFailure("源站未返回真实影片标题")
        picture = self._format_pic_url(self._extract_pic_from_soup(soup))
        address = self._extract_play_url(html, page_url=page_url)
        item = {"vod_id": value, "vod_name": title, "vod_pic": picture,
                "vod_play_from": "WanWuu" if address else "WanWuu-Sniff",
                "vod_play_url": "播放$" + (address or page_url)}
        category = self._video_category(value)
        if category:
            item["type_id"] = category
        return {"list": [item]}

    def searchContent(self, key, quick=False, pg="1"):
        """使用原站视频搜索的真实分页，并保留可从原站路径确定的分类。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, total=0)
        path = "/videos/search/%s%s/" % (quote(str(key)), "/page/%s" % page if page > 1 else "")
        items = self._parse_video_list(self._fetch_html_safe(path, allow_missing_page=page > 1))
        for item in items:
            category = self._video_category(item.get("vod_id"))
            if category:
                item["type_id"] = category
        return self.pageResult(items, page)

    def playerContent(self, flag, id, vipFlags=None):
        """已知媒体直连按原站请求头返回，网页交给通用静态解析器。"""
        value = str(id or "")
        if not value:
            return self.sourceFailure("源播放标识为空")
        direct = bool(re.search(r'\.(m3u8|mp4|flv|mkv)(?:\?|$)', value, re.I))
        if not direct and any(prefix in value for prefix in ("/videos/", "/ai/", "/porn/")):
            address = self._extract_play_url(self._fetch_html_safe(value), page_url=value)
            if address:
                value = address
                direct = bool(re.search(r'\.(m3u8|mp4|flv|mkv)(?:\?|$)', value, re.I))
        return {"parse": 0 if direct else 1, "url": value,
                "header": {"User-Agent": self.headers["User-Agent"], "Referer": self.site_url + "/", "Origin": self.site_url}}

    # ==================== HTML 解析辅助函数 ====================

    def _parse_hot_tags(self, html):
        """读取原站公开标签的名称和地址。"""
        tags = []
        if not html:
            return tags

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('a[href*="/search/"], a[href*="/tags/"], .tag-list a, .tag-cloud a')
        seen = set()

        for item in items:
            href = item.get('href', '').strip()
            vod_id = self._clean_vod_id(href)

            if not vod_id or vod_id in seen:
                continue

            tag_name = item.get_text(strip=True)
            if not tag_name or len(tag_name) < 2:
                continue

            vod_name = tag_name.lstrip('#').strip()

            seen.add(vod_id)
            tags.append({
                "vod_id": vod_id,
                "vod_name": f"🏷️ {vod_name}",
                "vod_pic": "",
                "vod_remarks": "热门标签",
                "vod_tag": "folder",
            })

        return tags

    def _parse_movieset_list(self, html):
        """解析原站合集目录，仅供源内部使用。"""
        sets = []
        if not html:
            return sets

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('ul.fc-albums-grid > li, li, div.album-item')
        seen = set()

        for item in items:
            a_tag = item.find('a', href=re.compile(r'^/moviesets/'))
            if not a_tag:
                continue

            vod_id = self._clean_vod_id(a_tag.get('href'))
            if not vod_id or vod_id in seen or vod_id.rstrip('/') == '/moviesets':
                continue

            img_tag = item.find('img')
            vod_name = ""
            if img_tag and img_tag.get('alt'):
                vod_name = img_tag.get('alt').strip()
            if not vod_name:
                p_title = item.select_one('p.line-clamp-2, .title, h3, h2')
                vod_name = p_title.get_text(strip=True) if p_title else ""

            raw_pic = self._extract_pic_from_node(item)
            vod_pic = self._format_pic_url(raw_pic)

            count_tag = item.select_one('span.rounded-full, .badge, .count')
            vod_remarks = count_tag.get_text(strip=True) if count_tag else "合集"

            if vod_id and vod_name:
                seen.add(vod_id)
                sets.append({
                    "vod_id": vod_id,
                    "vod_name": vod_name,
                    "vod_pic": vod_pic,
                    "vod_remarks": vod_remarks,
                    "vod_tag": "folder",
                })

        return sets

    def _parse_movieset_detail_videos(self, html):
        """读取原站合集中的真实视频链接。"""
        videos = []
        if not html:
            return videos

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('li.group, li, div.video-item')
        seen = set()

        for item in items:
            a_tag = item.find('a', href=re.compile(r'^/(videos|ai|porn|posts)/'))
            if not a_tag:
                continue

            vod_id = self._clean_vod_id(a_tag.get('href'))
            if not vod_id or vod_id in seen:
                continue

            img_tag = item.find('img')
            vod_name = ""
            if img_tag and img_tag.get('alt'):
                vod_name = img_tag.get('alt').strip()
            if not vod_name:
                title_el = item.select_one('.line-clamp-2, line-clamp-1, h2, h3, .title')
                vod_name = title_el.get_text(strip=True) if title_el else ""

            raw_pic = self._extract_pic_from_node(item)
            vod_pic = self._format_pic_url(raw_pic)

            vod_remarks = self._extract_video_remarks(item)

            if vod_id and vod_name:
                seen.add(vod_id)
                videos.append({
                    "vod_id": vod_id,
                    "vod_name": vod_name,
                    "vod_pic": vod_pic,
                    "vod_remarks": vod_remarks,
                })

        return videos

    def _parse_post_list(self, html):
        """解析原站帖子卡片，影视目录不发布这些条目。"""
        posts = []
        if not html:
            return posts

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('article, div.post-item, li.post-item, .post-list li, ul > li')
        if not items:
            items = soup.find_all('article')

        seen = set()

        for item in items:
            item_class = " ".join(item.get('class', []))
            if any(ad_kw in item_class.lower() for ad_kw in ('ad', 'sponsor', 'gg', 'banner', 'nav', 'menu', 'header', 'footer', 'category', 'tag')):
                continue

            a_tag = item.find('a', href=re.compile(r'^/posts/'))
            if not a_tag:
                continue

            href = a_tag.get('href', '').strip()
            clean_href = href.rstrip('/')

            excluded_paths = {
                '/posts', '/posts/all',
                '/posts/wanwu-changliao', '/posts/lianzu-yuanchuang',
                '/posts/doum-tiantang', '/posts/nvwang-tiandi'
            }
            if clean_href in excluded_paths or not re.search(r'/\d+', href):
                if clean_href in excluded_paths:
                    continue

            vod_id = self._clean_vod_id(href)
            if not vod_id or vod_id in seen:
                continue

            title_el = item.select_one('h2, h3, .title, .post-title, .font-bold')
            vod_name = title_el.get_text(strip=True) if title_el else a_tag.get_text(strip=True)
            if not vod_name or len(vod_name) < 2:
                continue

            if vod_name in ['玩物畅聊', '恋足原创', '抖M天堂', '女王天地', '全部', '最新', '热门']:
                continue

            raw_pic = self._extract_pic_from_node(item)
            vod_pic = self._format_pic_url(raw_pic)

            desc_el = item.select_one('.post-item-desc, .author-info, .text-gray-400, .metadata')
            if desc_el:
                spans = [s.get_text(strip=True) for s in desc_el.find_all(['span', 'div']) if s.get_text(strip=True)]
                vod_remarks = " · ".join(spans[:2]) if spans else desc_el.get_text(strip=True)
            else:
                vod_remarks = "社区帖子"

            seen.add(vod_id)
            posts.append({
                "vod_id": vod_id,
                "vod_name": vod_name,
                "vod_pic": vod_pic,
                "vod_remarks": vod_remarks,
            })

        return posts

    def _parse_novel_list(self, html):
        """解析原站小说卡片，影视目录不发布这些条目。"""
        novels = []
        if not html:
            return novels

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('article, li, div.card, div.novel-item')
        seen = set()

        for item in items:
            a_tag = item.find('a', href=re.compile(r'^/novels/'))
            if not a_tag:
                continue

            vod_id = self._clean_vod_id(a_tag.get('href'))
            if not vod_id or vod_id in seen or vod_id.rstrip('/') in ('/novels', '/novels/new', '/novels/popular', '/novels/hot'):
                continue

            vod_name = a_tag.get('title') or a_tag.get_text(strip=True)
            if not vod_name:
                title_el = item.select_one('h2, h3, .title')
                vod_name = title_el.get_text(strip=True) if title_el else ""

            raw_pic = self._extract_pic_from_node(item)
            vod_pic = self._format_pic_url(raw_pic)

            if vod_id and vod_name:
                seen.add(vod_id)
                novels.append({
                    "vod_id": vod_id,
                    "vod_name": vod_name,
                    "vod_pic": vod_pic,
                    "vod_remarks": "小说",
                })

        return novels

    # ==================== 时间优先拼接（时间作主要副标题，角标作后缀） ====================

    def _extract_video_remarks(self, item):
        """将时间设为主显示（副标题），分类与角标紧随其后"""
        time_text = ""
        badge_tags = []

        # 1. 优先提取时间（例如："29:09"）
        duration_el = item.select_one('div[class*="bottom-0"] div, .duration, .time, .opacity-50')
        if duration_el:
            time_text = duration_el.get_text(strip=True)

        # 兜底：如果选择器没找到，抓取符合 \d+:\d+ 的文字
        if not time_text or not re.search(r'\d+:\d+', time_text):
            all_text = item.get_text()
            match = re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', all_text)
            if match:
                time_text = match.group(0)

        # 2. 提取角标（例如："直播回放"）
        badge_el = item.select_one('div[class*="top-0"], div[class*="absolute"][class*="left-0"], .badge, .label')
        if badge_el:
            text = badge_el.get_text(strip=True)
            if text and len(text) <= 8 and text != time_text:
                badge_tags.append(text)

        # 3. 提取其他辅助分类标签
        sub_a_tags = item.select('.dx-subtitle a, div[class*="subtitle"] a, a[href*="/search/"]')
        for a in sub_a_tags:
            t = a.get_text(strip=True)
            if t and t != time_text and t not in badge_tags:
                badge_tags.append(t)
            if len(badge_tags) >= 2:
                break

        # 4. 拼接组合：[时间] | [角标1] | [角标2]
        result_parts = []
        if time_text:
            result_parts.append(time_text)

        result_parts.extend(badge_tags)

        if result_parts:
            return " | ".join(result_parts)

        return "HD"

    def _parse_video_list(self, html):
        """解析真实视频卡片并移除同页重复链接。"""
        videos = []
        if not html:
            return videos

        soup = BeautifulSoup(html, 'html.parser')
        items = soup.select('li.group')
        if not items:
            items = soup.select('li, div.video-item')

        seen = set()

        for item in items:
            a_tag = item.find('a', href=re.compile(r'^/(videos|ai|porn|posts)/'))
            if not a_tag:
                continue

            vod_id = self._clean_vod_id(a_tag.get('href'))
            if not vod_id or vod_id in seen or "/search/" in vod_id:
                continue

            img_tag = item.find('img')
            vod_name = ""
            if img_tag and img_tag.get('alt'):
                vod_name = img_tag.get('alt').strip()
            if not vod_name:
                title_el = item.select_one('a.line-clamp-2, a.line-clamp-1, .line-clamp-2, .line-clamp-1, h2, h3, .title')
                vod_name = title_el.get_text(strip=True) if title_el else ""

            raw_pic = self._extract_pic_from_node(item)
            vod_pic = self._format_pic_url(raw_pic)

            # 调用调整后的提取函数
            vod_remarks = self._extract_video_remarks(item)

            if vod_id and vod_name:
                seen.add(vod_id)
                videos.append({
                    "vod_id": vod_id,
                    "vod_name": vod_name,
                    "vod_pic": vod_pic,
                    "vod_remarks": vod_remarks,
                })

        return videos

    # ==================== 图片代理 & 解密模块 ====================

    def _clean_vod_id(self, href):
        """从原站链接提取稳定视频标识。"""
        if not href:
            return ""
        href = href.strip()
        for d in self.DOMAINS:
            if href.startswith(d):
                href = href[len(d):]
        return href

    def _extract_pic_from_node(self, node):
        """从真实卡片节点读取封面地址。"""
        data_node = node.select_one('[data-src], [data-original]')
        if data_node:
            val = data_node.get('data-src') or data_node.get('data-original')
            if val and "loading" not in val:
                return val.strip()

        img_tag = node.find('img')
        if img_tag:
            for attr in ("data-src", "data-original", "data-lazy-src", "src"):
                val = img_tag.get(attr, "").strip()
                if val and "poster_loading" not in val and "loading.svg" not in val:
                    return val

        bg_node = node.select_one('[style*="background-image"]')
        if bg_node:
            style_str = bg_node.get('style', '')
            bg_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style_str)
            if bg_match:
                return bg_match.group(1).strip()

        return ""

    def _extract_pic_from_soup(self, soup):
        """从真实详情节点读取封面地址。"""
        poster_el = soup.select_one('.post-item-poster, [data-src]')
        if poster_el:
            val = poster_el.get('data-src') or poster_el.get('src') or ""
            if val and "poster_loading" not in val:
                return val
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
        return ""

    def localProxy(self, param):
        """处理本源显式发布的代理资源。"""
        raw_url = param.get("url") or param.get("pic") or ""
        if not raw_url:
            return [404, "text/plain", "Missing URL Parameter"]

        url = unquote(raw_url)
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = f"{self.site_url}{url}"

        img_headers = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": f"{self.site_url}/",
            "Origin": self.site_url,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        try:
            res = self.fetch(url, headers=img_headers, allow_redirects=True, timeout=15)
            content = res.content if res else b""

            if not content or len(content) < 100:
                return [404, "text/plain", "Image too small or blocked"]

            if self._is_valid_image_header(content):
                ct = self._detect_image_type(content)
                return [200, ct, content]

            decrypted = self._try_aes_decrypt(content)
            if decrypted and self._is_valid_image_header(decrypted):
                ct = self._detect_image_type(decrypted)
                return [200, ct, decrypted]

            fixed = self._try_fix_image(content)
            if fixed and self._is_valid_image_header(fixed):
                ct = self._detect_image_type(fixed)
                return [200, ct, fixed]

            return [200, "application/octet-stream", content]
        except Exception:
            return [502, "text/plain; charset=utf-8", "源图片读取失败"]

    def _format_pic_url(self, raw_pic_url):
        """生成由宿主转接的本源图片代理地址。"""
        if not raw_pic_url:
            return ""
        raw_pic_url = raw_pic_url.strip()
        if raw_pic_url.startswith("//"):
            raw_pic_url = f"https:{raw_pic_url}"
        elif raw_pic_url.startswith("/"):
            raw_pic_url = f"{self.site_url}{raw_pic_url}"

        proxy_base = self.getProxyUrl()
        if proxy_base:
            safe_url = quote_plus(raw_pic_url)
            return f"{proxy_base}&action=pic&url={safe_url}"
        else:
            return f"{raw_pic_url}@Referer={self.site_url}/"

    def _detect_image_type(self, data):
        """根据图片字节标记判断真实图片格式。"""
        if not data or len(data) < 12:
            return "application/octet-stream"
        if data[:4] == b'\x89PNG':
            return "image/png"
        if data[:2] == b'\xff\xd8':
            return "image/jpeg"
        if data[:4] == b'RIFF' and len(data) > 12 and data[8:12] == b'WEBP':
            return "image/webp"
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return "image/gif"
        if data[:2] == b'BM':
            return "image/bmp"
        return "application/octet-stream"

    def _is_valid_image_header(self, data):
        """检查实际图片文件头，不把错误页当成图片。"""
        return self._detect_image_type(data) != "application/octet-stream"

    def _try_fix_image(self, data):
        """按源站现有图片格式处理附加字节。"""
        if not data or len(data) < 100:
            return None

        for skip in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024):
            if len(data) > skip + 4:
                sub = data[skip:skip+4]
                if sub in (b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1', b'\x89PNG', b'RIFF'):
                    return data[skip:]

        inverted = bytes([b ^ 0xFF for b in data])
        if self._is_valid_image_header(inverted):
            return inverted

        return None

    def _try_aes_decrypt(self, data):
        """按原站图片公开格式解码已有图片响应。"""
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError:
            return None

        if not data or len(data) < 32:
            return None

        candidates = [
            (b'f5d965df75336270', b'97b60394abc2fbe1'),
            (b'75336270f5d965df', b'abc2fbe197b60394'),
            (b'f5d965df75336270', b'f5d965df75336270'),
            (b'f5d965df75336270', None),
        ]

        rem = len(data) % 16
        data_to_try = [data]
        if rem != 0:
            data_to_try.append(data[:-rem])

        for chunk in data_to_try:
            for key, iv in candidates:
                try:
                    if iv:
                        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                    else:
                        cipher = AES.new(key, AES.MODE_ECB)

                    decrypted = cipher.decrypt(chunk)

                    try:
                        decrypted_unpadded = unpad(decrypted, AES.block_size)
                        if self._is_valid_image_header(decrypted_unpadded):
                            return decrypted_unpadded
                    except Exception:
                        pass

                    if self._is_valid_image_header(decrypted):
                        return decrypted
                except Exception:
                    continue

        return None

    def _video_category(self, value):
        """仅使用原站明确的视频路径归类，不猜测全局搜索结果类型。"""
        path = str(value or "")
        for prefix, category in (("/videos/", "videos_zhibo"), ("/ai/", "ai_all"), ("/porn/", "porn_all")):
            if prefix in path:
                return category
        return ""
