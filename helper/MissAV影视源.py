"""
==================================================
@Spider Name : MissAV
@Author      : 飞鱼
@Version     : 1.0.0
@Description : TVBox/CatVod MissAV Spider Plugin
==================================================
"""

import json
import sys
import re
import base64
from urllib.parse import urljoin, quote, unquote
from pyquery import PyQuery as pq

# 兼容 Spider 基础类导入
sys.path.append('..')
from base.spider import Spider

class Spider(Spider):
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = 'MissAV影视'

    author = "飞鱼"
    version = "1.0.0"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    # 轮询域名列表（优先使用 missav02.xyz，失败自动切换 missav.app）
    domains = [
        'https://missav02.xyz',
        'https://missav.app'
    ]

    def getDependence(self):
        """声明本源使用的已知依赖，不执行依赖安装。"""
        return ['pyquery']

    def getName(self):
        """返回本独立插件的固定名称。"""
        return 'MissAV影视'

    def init(self, extend=""):
        # 日志控制台输出作者版权提示
        """初始化独立内容源配置，保留原站正常访问规则。"""
        print(f"[{self.getName()}] 插件加载成功 | 作者: {self.author} | 版本: {self.version}")

    def isVideoFormat(self, url):
        """保留格式检查入口，最终格式由真实媒体结果判断。"""
        pass

    def manualVideoCheck(self):
        """保留手动检查入口，不触发媒体下载。"""
        pass

    def action(self, action):
        """保留源动作接口，不执行额外用户操作。"""
        pass

    # 核心轮询请求工具：支持多域名故障转移
    def _fetch_with_fallback(self, path):
        """依次读取配置域名，全部失败时明确报告访问受限。"""
        for domain in self.domains:
            try:
                response = self.fetch(urljoin(domain, path), headers=self.headers, timeout=15)
                if response.status_code == 200 and response.text.strip() and 'Just a moment...' not in response.text:
                    return response.text, domain
            except Exception:
                continue
        return self.sourceFailure("源站域名不可访问或需要浏览器验证")

    # 1. 首页推荐数据 & 分类菜单
    def homeContent(self, filter):
        """返回原站分类目录和可用筛选项。"""
        result = {}

        # 定义分类
        classes = [
            {"type_name": "国产", "type_id": "20"},
            {"type_name": "日本有码", "type_id": "21"},
            {"type_name": "日本无码", "type_id": "22"},
            {"type_name": "中文字幕", "type_id": "28"},
            {"type_name": "欧美", "type_id": "23"},
            {"type_name": "动漫", "type_id": "24"},
            {"type_name": "伦理", "type_id": "25"}
        ]
        result['class'] = classes

        # 筛选配置
        filters = {
            "20": [{"key": "cateId", "name": "分类", "value": [{"v": "20", "n": "全部"}, {"v": "26", "n": "国产精品"}, {"v": "27", "n": "国产剧情"}, {"v": "29", "n": "国产自拍"}, {"v": "35", "n": "国产主播"}, {"v": "85", "n": "国模私拍"}, {"v": "91", "n": "网红明星"}, {"v": "105", "n": "国产SM"}, {"v": "107", "n": "台湾辣妹"}, {"v": "108", "n": "香港正妹"}]}],
            "21": [{"key": "cateId", "name": "分类", "value": [{"v": "21", "n": "全部"}, {"v": "31", "n": "人妻"}, {"v": "44", "n": "素人"}, {"v": "46", "n": "口爆颜射"}, {"v": "47", "n": "萝莉少女"}, {"v": "48", "n": "美乳巨乳"}, {"v": "52", "n": "制服诱惑"}, {"v": "57", "n": "调教"}, {"v": "58", "n": "出轨"}, {"v": "101", "n": "有码精品"}]}],
            "22": [{"key": "cateId", "name": "分类", "value": [{"v": "22", "n": "全部"}, {"v": "102", "n": "无码精品"}]}],
            "23": [{"key": "cateId", "name": "分类", "value": [{"v": "23", "n": "全部"}, {"v": "104", "n": "欧美精品"}]}],
            "24": [{"key": "cateId", "name": "分类", "value": [{"v": "24", "n": "全部"}, {"v": "103", "n": "动漫精品"}]}],
            "25": [{"key": "cateId", "name": "分类", "value": [{"v": "25", "n": "全部"}, {"v": "39", "n": "综合三级"}]}],
            "28": [{"key": "cateId", "name": "分类", "value": [{"v": "28", "n": "全部"}, {"v": "51", "n": "日本中字"}]}]
        }
        if filter:
            result['filters'] = filters

        # 轮询获取首页推荐视频
        try:
            html, base_domain = self._fetch_with_fallback("/label/new/")
            if html:
                doc = pq(html)
                videos = self._parse_cards(doc, base_domain)
                result['list'] = videos
            else:
                result['list'] = []
        except Exception:
            result['list'] = []

        return result

    def homeVideoContent(self):
        """读取原站首页的真实影片卡片。"""
        return self.homeContent(False)

    # 2. 分类列表数据处理
    def categoryContent(self, tid, pg, filter, extend):
        """按源站真实分类与页码请求，不返回固定的假总量。"""
        page = max(1, int(pg or 1))
        category_id = str((extend or {}).get("cateId") or tid)
        html, domain = self._fetch_with_fallback("/vodtype/%s-%s/" % (category_id, page))
        items = self._parse_cards(pq(html), domain)
        for item in items:
            item["type_id"] = category_id
        return self.pageResult(items, page, page_size=20)

    # 3. 详情页解析
    def detailContent(self, array):
        """读取真实影片详情和原站发布的分集线路。"""
        vod_id = array[0]

        try:
            path = vod_id
            for d in self.domains:
                if path.startswith(d):
                    path = path.replace(d, '')
                    break

            html, base_domain = self._fetch_with_fallback(path)
            if not html:
                return {'list': []}

            doc = pq(html)

            title = doc('meta[property="og:title"]').attr('content') or doc('title').text()
            pic = doc('img.w-full').attr('src') or doc('img').attr('data-src') or ''

            type_name = doc('.text-muted a').text()
            actor = ''
            if '</a>：' in html and '</p>' in html:
                try:
                    actor = html.split('</a>：')[1].split('</p>')[0].replace('<a>', '').replace('</a>', '').strip()
                except Exception:
                    actor = ''

            remarks = ''
            if '概要：</span>' in html and '</p>' in html:
                try:
                    remarks = html.split('概要：</span>')[1].split('</p>')[0].strip()
                except Exception:
                    remarks = ''

            play_url = urljoin(base_domain, path)
            vod_play_from = f"MissAV直连 ({self.author})"
            vod_play_url = f"正片播放${play_url}"

            vod = {
                'vod_id': vod_id,
                'vod_name': title,
                'vod_pic': pic,
                'type_name': type_name,
                'vod_actor': actor,
                'vod_content': remarks,
                'vod_play_from': vod_play_from,
                'vod_play_url': vod_play_url
            }

            return {'list': [vod]}
        except Exception:
            return {'list': []}

    # 4. 搜索处理
    def searchContent(self, key, quick, pg="1"):
        """把 page 送入原站搜索参数，避免每页都查询第一页。"""
        page = max(1, int(pg or 1))
        if not str(key).strip():
            return self.pageResult([], page, page, 20, 0)
        path = "/vodsearch/%s-------------/?page=%s" % (quote(str(key)), page)
        html, domain = self._fetch_with_fallback(path)
        return self.pageResult(self._parse_cards(pq(html), domain), page, page_size=20)

    # 5. 播放接口：优先读取真实媒体，网页由宿主有限静态解析。
    def playerContent(self, flag, id, vipFlags):
        # 关键变量：真实媒体直链直接返回，避免把 MP4 当网页完整下载。
        """按真实分集标识读取媒体地址，网页交由宿主解析。"""
        if re.search(r"\.(m3u8|mp4|flv|mkv|webm)(?:\?|$)", str(id or ""), re.I):
            headers = self.headers
            return {"parse": 0, "url": str(id), "header": headers}
        result = {
            'parse': '1',
            'playUrl': '',
            'url': id,
            'header': json.dumps(self.headers)
        }

        try:
            rsp = self.fetch(id, headers=self.headers)
            html = rsp.text if rsp else ""

            match = re.search(r'var\s+player_aaaa\s*=\s*(\{.*?\});', html)
            if match:
                player_json = json.loads(match.group(1))
                raw_url = player_json.get('url', '')
                encrypt = player_json.get('encrypt', 0)

                if encrypt == 1:
                    raw_url = unquote(raw_url)
                elif encrypt == 2:
                    raw_url = unquote(base64.b64decode(raw_url).decode('utf-8'))

                if raw_url and ('.m3u8' in raw_url or '.mp4' in raw_url or 'http' in raw_url):
                    result['parse'] = '0' if re.search(r'\.(m3u8|mp4|flv|mkv)(?:\?|$)', raw_url, re.I) else '1'
                    result['url'] = raw_url
        except Exception:
            pass

        return result

    # 辅助工具：提取视频列表卡片
    def _parse_cards(self, doc, base_domain):
        """从原站页面节点读取真实影片卡片。"""
        videos = []
        items = doc('body .gap-5 .group').items()

        for item in items:
            link = item('a').attr('href')
            if not link:
                continue

            vod_id = link if link.startswith('http') else urljoin(base_domain, link)
            title = item('.text-nord4').text().strip()
            img = item('img').attr('data-src') or item('img').attr('src') or ''

            absolute_els = item('.absolute')
            remarks = ""
            if len(absolute_els) >= 2:
                sub1 = pq(absolute_els[1]).text().strip()
                sub0 = pq(absolute_els[0]).text().strip()
                remarks = f"{sub1} {sub0}".strip()
            elif len(absolute_els) == 1:
                remarks = pq(absolute_els[0]).text().strip()

            videos.append({
                'vod_id': vod_id,
                'vod_name': title,
                'vod_pic': img,
                'vod_remarks': remarks
            })

        return videos
