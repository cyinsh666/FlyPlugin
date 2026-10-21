# -*- coding: utf-8 -*-
"""
================================================================================
 TVBox / CatVod 系 -- Python 源「果果短剧」
================================================================================
 目标站点: https://app.ggduanju.com  (果果短剧)
 API 后端:  https://baidu.com.ifengying.cn/  (DSVod 模板，AES-CBC 加密响应)

 接口清单:
   GET  index.php/dsv1.api/config     -- 全站配置(分类表/筛选项/播放器配置)
   POST index.php/dsv1.vod/show       -- 分类列表 {id, pid, by, page, token, class?, area?, year?, lang?}
   POST index.php/dsv1.vod/search     -- 搜索   {wd, page, token}
   GET  index.php/dsv1.vod/player     -- 详情   {id, token}
   POST index.php/dsv1.vod/rank       -- 排行   {type, page, token}

 响应解密: AES-CBC, key=IV='EFCD8D3D1DAA31F4', padding=PKCS7, base64 密文

 播放地址: vod_play_url 里的链接均为直链 m3u8, parse=0 即可播放

【合规提醒】仅供技术交流与个人学习。请遵守目标站点服务条款与 robots.txt,
 控制请求频率, 不要对服务器造成压力; 不要采集、传播受版权保护的内容。
================================================================================
"""

import os
import re
import sys
import json
import time
import base64

from urllib.parse import quote, urljoin, unquote

# ================================================================================
# 一、可选依赖探测
# ================================================================================
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from pyquery import PyQuery as pq
    _HAS_PYQUERY = True
except ImportError:
    _HAS_PYQUERY = False

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


# ================================================================================
# 二、基类导入兼容
# ================================================================================
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(_THIS_DIR)
sys.path.append(os.path.join(_THIS_DIR, '..'))

from base.spider import Spider
_BASE_INFO = 'base.spider.Spider'


# ================================================================================
# 三、源主体
# ================================================================================
class Spider(Spider):

    # ============================================================================
    # 配置区
    # ============================================================================
    # 关键变量：固定插件名称用于宿主静态识别，不执行源站请求。
    display_name = '果果短剧'

    HOST = 'https://baidu.com.ifengying.cn'   # API 后端地址
    NAME = '果果短剧'                          # 源显示名
    AES_KEY = b'EFCD8D3D1DAA31F4'              # AES-CBC 密钥(同时用作 IV)
    API_PREFIX = '/index.php/dsv1.'            # URL 前缀: {host}{prefix}{action}

    # -- 分类表: 显示名 -> type_id --
    CATES = {
        '短剧': '1',
        '电影': '2',
        '电视剧': '3',
        '动漫': '4',
    }

    # -- 电影子类型 (pid) --
    MOVIE_SUB = {
        '动作片': '6', '喜剧片': '7', '爱情片': '8', '科幻片': '9',
        '恐怖片': '10', '剧情片': '11', '战争片': '12',
    }

    # -- 电视剧子类型 (pid) --
    TV_SUB = {
        '国产剧': '13', '港台剧': '14', '日韩剧': '15', '欧美剧': '16',
    }

    # -- 播放线路代码 -> 显示名 --
    LINE_NAMES = {
        'wwm3u8': '望望线路',
        'modum3u8': '魔都线路',
        '1080zyk': '幼稚线路',
        'bfzym3u8': '暴风线路',
        'ffm3u8': '非凡线路',
        'dyttm3u8': '天堂线路',
        'tym3u8': '天涯线路',
        'ffzy': '非凡资源',
        'lzm3u8': '量子线路',
        'snm3u8': '索尼线路',
        'takm3u8': '天空线路',
    }

    # -- 分类筛选配置 (type_id -> 支持的筛选维度) --
    FILTER_CONFIG = {
        '1': ['class'],
        '2': ['class', 'area', 'year'],
        '3': ['class', 'area', 'year'],
        '4': ['class', 'area', 'year'],
    }

    # -- 各分类的 class 选项 --
    CLASS_OPTIONS = {
        '1': ['重生民国', '穿越年代', '现代言情', '反转爽文', '女恋总裁', '闪婚离婚', '都市脑洞', '古装仙侠'],
        '2': ['爱情', '喜剧', '动作', '剧情', '科幻', '战争', '奇幻', '音乐', '西部', '历史',
              '恐怖', '动画', '传记', '悬疑', '歌舞', '犯罪', '武侠', '惊悚', '冒险', '灾难', '家庭'],
        '3': ['古装', '战争', '喜剧', '家庭', '犯罪', '动作', '奇幻', '剧情', '历史', '商战'],
        '4': ['科幻', '热血', '推理', '搞笑', '冒险', '动作', '少女', '益智'],
    }

    # -- 地区选项 --
    AREA_OPTIONS = ['大陆', '香港', '台湾', '日本', '韩国', '美国', '泰国', '新加坡', '其他']

    # -- 年份选项 --
    YEAR_OPTIONS = ['2026', '2025', '2024', '2023', '2022', '2021', '2020',
                    '2019', '2018', '2017', '2016', '2015', '2014', '2013',
                    '2012', '2011', '2010']

    # -- 网络参数 --
    TIMEOUT = 15
    RETRY = 2
    SLEEP_BASE = 1
    ENCODING = 'utf-8'

    # ============================================================================
    # 四、生命周期方法
    # ============================================================================
    def init(self, extend=""):
        """初始化独立内容源配置，保留原站正常访问规则。"""
        self.host = extend if (extend and extend.startswith('http')) else self.HOST
        self.host = self.host.rstrip('/')

        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
            'Referer': 'https://app.ggduanju.com/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://app.ggduanju.com',
        }

        self.session = None
        if _HAS_REQUESTS:
            self.session = requests.Session()
            self.session.headers.update(self.headers)

        self._dbg('init done: base=%s | requests=%s | crypto=%s'
                  % (_BASE_INFO, _HAS_REQUESTS, _HAS_CRYPTO))

    def getName(self):
        """返回本独立插件的固定名称。"""
        return '果果短剧'

    def getDependence(self):
        """声明本源使用的已知依赖，不执行依赖安装。"""
        return []

    def destroy(self):
        """释放本源的会话或缓存。"""
        pass

    # ============================================================================
    # 五、工具方法
    # ============================================================================
    def _dbg(self, *messages):
        """记录调用事件，不把播放地址、Cookie 或源接口密钥写入日志。"""
        self.log("果果源调用", *[type(message).__name__ for message in messages])

    def _aes_decrypt(self, text):
        """按原站公开 AES 协议解密；解密失败不返回假空对象。"""
        if not _HAS_CRYPTO:
            raise ModuleNotFoundError("Crypto")
        try:
            raw = base64.b64decode(str(text).strip(), validate=True)
            cipher = AES.new(self.AES_KEY, AES.MODE_CBC, self.AES_KEY)
            value = json.loads(unpad(cipher.decrypt(raw), AES.block_size).decode("utf-8"))
            if not isinstance(value, dict):
                return self.sourceFailure("源站 JSON 结构无效")
            if value.get("code") != 0:
                return self.sourceFailure("源站接口返回业务失败")
            return value
        except Exception:
            return self.sourceFailure("源站响应解密或业务校验失败")

    def _api_url(self, action):
        """构造 API URL"""
        return self.host + self.API_PREFIX + action

    def _api_get(self, action, params=None):
        """GET 原站公开目录接口，网络错误不表现为搜索或目录为空。"""
        try:
            response = self.fetch(self._api_url(action), params=params, headers=self.headers, timeout=self.TIMEOUT)
            response.raise_for_status()
            return self._aes_decrypt(response.text)
        except Exception:
            return self.sourceFailure("源站目录接口读取失败")

    def _api_post(self, action, data=None):
        """POST 原站只读分类/搜索接口，保留实际分页数据。"""
        try:
            response = self.post(self._api_url(action), data=data or {}, headers=self.headers, timeout=self.TIMEOUT)
            response.raise_for_status()
            return self._aes_decrypt(response.text)
        except Exception:
            return self.sourceFailure("源站分类或搜索接口读取失败")

    def fixUrl(self, u):
        """相对路径补全"""
        if not u:
            return ''
        u = u.strip()
        if u.startswith('http://') or u.startswith('https://'):
            return u
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            return self.host + u
        return urljoin(self.host + '/', u)

    def cleanText(self, src):
        """清洗文本: 去 HTML 标签、HTML 实体、压空白"""
        if not src:
            return ''
        src = str(src)
        # HTML 实体转换
        entities = {
            '&ldquo;': '"', '&rdquo;': '"', '&lsquo;': "'", '&rsquo;': "'",
            '&hellip;': '...', '&amp;': '&', '&nbsp;': ' ',
            '&lt;': '<', '&gt;': '>', '&quot;': '"',
        }
        for k, v in entities.items():
            src = src.replace(k, v)
        # 去标签
        src = re.sub(r'<[^>]+>', '', src)
        return re.sub(r'\s+', ' ', src).strip()

    def _map_line_name(self, code):
        """线路代码 -> 显示名"""
        return self.LINE_NAMES.get(code, code)

    # ============================================================================
    # 六、homeContent: 分类条与筛选器
    # ============================================================================
    def homeContent(self, filter):
        """返回原站分类目录和可用筛选项。"""
        result = {'class': [], 'filters': {}}

        # -- 主分类 --
        for name, tid in self.CATES.items():
            result['class'].append({'type_id': str(tid), 'type_name': name})

        # -- 子分类 (作为独立分类入口) --
        for name, pid in self.MOVIE_SUB.items():
            result['class'].append({'type_id': '2_%s' % pid, 'type_name': name})
        for name, pid in self.TV_SUB.items():
            result['class'].append({'type_id': '3_%s' % pid, 'type_name': name})

        # -- 筛选器: 只为主分类 1-4 生成 --
        for tid, dims in self.FILTER_CONFIG.items():
            filters = []

            # 子分类 (pid) - 仅电影和电视剧
            if tid == '2':
                sub_values = [{'n': '全部', 'v': ''}]
                for sn, sp in self.MOVIE_SUB.items():
                    sub_values.append({'n': sn, 'v': sp})
                filters.append({'key': 'pid', 'name': '类型', 'value': sub_values})
            elif tid == '3':
                sub_values = [{'n': '全部', 'v': ''}]
                for sn, sp in self.TV_SUB.items():
                    sub_values.append({'n': sn, 'v': sp})
                filters.append({'key': 'pid', 'name': '类型', 'value': sub_values})

            # class (分类)
            if 'class' in dims:
                vals = [{'n': '全部', 'v': ''}]
                for c in self.CLASS_OPTIONS.get(tid, []):
                    vals.append({'n': c, 'v': c})
                filters.append({'key': 'class', 'name': '类型', 'value': vals})

            # area (地区)
            if 'area' in dims:
                vals = [{'n': '全部', 'v': ''}]
                for a in self.AREA_OPTIONS:
                    vals.append({'n': a, 'v': a})
                filters.append({'key': 'area', 'name': '地区', 'value': vals})

            # year (年份)
            if 'year' in dims:
                vals = [{'n': '全部', 'v': ''}]
                for y in self.YEAR_OPTIONS:
                    vals.append({'n': y, 'v': y})
                filters.append({'key': 'year', 'name': '年份', 'value': vals})

            # by (排序)
            filters.append({
                'key': 'by', 'name': '排序', 'value': [
                    {'n': '最新', 'v': 'time'},
                    {'n': '最热', 'v': 'hits'},
                ]
            })

            result['filters'][tid] = filters

        return result

    def homeVideoContent(self):
        """首页推荐: 取短剧分类第一页"""
        return self.categoryContent('1', '1', {}, {})

    # ============================================================================
    # 七、categoryContent: 分类列表页
    # ============================================================================
    def categoryContent(self, tid, pg, filter, extend):
        """根据原站分类标识与页码读取影片列表。"""
        result = {'list': [], 'page': 1, 'pagecount': 1, 'limit': 20, 'total': 0}
        page = int(pg) if pg else 1
        result['page'] = page

        try:
            # 解析 tid: "1" 或 "2_6" (父_子)
            tid = str(tid)
            if '_' in tid:
                parts = tid.split('_', 1)
                type_id = parts[0]
                pid = parts[1]
            else:
                type_id = tid
                pid = (extend or {}).get('pid', '')

            # 构造 POST 参数
            data = {
                'id': type_id,
                'pid': pid or '',
                'by': (extend or {}).get('by', ''),
                'page': page,
                'token': '',
            }

            # 筛选参数
            for k in ('class', 'area', 'year', 'lang'):
                v = (extend or {}).get(k, '')
                if v:
                    data[k] = v

            resp = self._api_post('vod/show', data)
            if not resp or resp.get('code') != 0:
                self._dbg('vod/show error: code=%s msg=%s' % (resp.get('code', '?'), resp.get('msg', '')))
                return result

            videos = []
            for item in resp.get('list', []):
                videos.append({
                    'vod_id': str(item.get('vod_id', '')),
                    'vod_name': self.cleanText(item.get('vod_name', '')),
                    'type_name': self.cleanText(item.get('type_name', '')),
                    'vod_pic': item.get('vod_pic', '') or item.get('vod_pic_thumb', ''),
                    'vod_remarks': self.cleanText(item.get('vod_remarks', '')),
                })

            result['list'] = videos
            result['pagecount'] = int(resp.get('pagecount', 1) or 1)
            result['limit'] = int(resp.get('limit', len(videos)) or len(videos))
            result['total'] = int(resp.get('total', len(videos)) or len(videos))

        except Exception as e:
            self._dbg('categoryContent error:', e)

        return result

    # ============================================================================
    # 八、detailContent: 详情页与播放线路
    # ============================================================================
    def detailContent(self, ids):
        """读取真实影片详情和原站发布的分集线路。"""
        result = {'list': []}
        if not ids:
            return result
        vid = str(ids[0])

        vod = {
            'vod_id': vid,
            'vod_name': '',
            'vod_pic': '',
            'vod_content': '',
            'vod_remarks': '',
            'vod_year': '',
            'vod_area': '',
            'vod_actor': '',
            'vod_director': '',
            'type_name': '',
            'vod_play_from': '',
            'vod_play_url': '',
        }

        try:
            resp = self._api_get('vod/player', {'id': vid, 'token': ''})
            if not resp or resp.get('code') != 0:
                self._dbg('vod/player error: code=%s' % resp.get('code', '?'))
                return result

            info = resp.get('info', {})
            if not info:
                return result

            vod['vod_name'] = self.cleanText(info.get('vod_name', ''))
            vod['vod_pic'] = info.get('vod_pic', '') or info.get('vod_pic_thumb', '')
            vod['vod_content'] = self.cleanText(info.get('vod_content', ''))
            vod['vod_remarks'] = self.cleanText(info.get('vod_remarks', ''))
            vod['vod_year'] = self.cleanText(info.get('vod_year', ''))
            vod['vod_area'] = self.cleanText(info.get('vod_area', ''))
            vod['vod_actor'] = self.cleanText(info.get('vod_actor', ''))
            vod['vod_director'] = self.cleanText(info.get('vod_director', ''))
            vod['type_name'] = self.cleanText(info.get('type_name', ''))

            # -- 播放线路 --
            play_from_raw = info.get('vod_play_from', '')
            play_url_raw = info.get('vod_play_url', '')

            if play_from_raw and play_url_raw:
                # 拆线路
                from_list = play_from_raw.split('$$$')
                url_list = play_url_raw.split('$$$')

                # 线路代码 -> 显示名
                display_names = [self._map_line_name(code) for code in from_list]

                # 如果线路数与集数组数不一致, 用原始值兜底
                if len(display_names) != len(url_list):
                    self._dbg('line count mismatch: from=%d url=%d' % (len(from_list), len(url_list)))
                    # 对齐: 缺少的部分用默认值
                    while len(display_names) < len(url_list):
                        display_names.append('线路%d' % (len(display_names) + 1))
                    url_list = url_list[:len(display_names)]

                vod['vod_play_from'] = '$$$'.join(display_names)
                vod['vod_play_url'] = '$$$'.join(url_list)
            else:
                # 兜底
                return self.sourceFailure('源站详情未返回真实播放列表')

            # 自检
            n_from = len(vod['vod_play_from'].split('$$$'))
            n_url = len(vod['vod_play_url'].split('$$$'))
            self._dbg('detail OK: lines=%d episodes_groups=%d' % (n_from, n_url))

            result['list'].append(vod)
        except Exception as e:
            self._dbg('detailContent error:', e)

        return result

    # ============================================================================
    # 九、playerContent: 拿到能播的真实地址
    # ============================================================================
    def playerContent(self, flag, id, vipFlags=''):
        """按真实分集标识读取媒体地址，网页交由宿主解析。"""
        if not id:
            return {'parse': 0, 'url': '', 'header': {}}

        play_url = id if str(id).startswith('http') else self.fixUrl(id)
        header = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': self.host + '/',
        }

        # m3u8 / mp4 直链 -> parse=0
        if re.search(r'\.(m3u8|mp4)(\?|$)', play_url, re.I):
            return {'parse': 0, 'url': play_url, 'jx': 0, 'header': header}

        # 非 m3u8/mp4 链接, 尝试从播放页提取
        try:
            if _HAS_REQUESTS and self.session is not None:
                r = self.session.get(play_url, timeout=self.TIMEOUT)
                if r and r.status_code == 200:
                    html = r.text
                    # 正则抠 m3u8
                    for pat in [
                        r'\burl\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                        r'\bsrc\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                        r'"(https?://[^"]+\.m3u8[^"]*)"',
                    ]:
                        m = re.search(pat, html, re.DOTALL)
                        if m:
                            return {'parse': 0, 'url': m.group(1), 'jx': 0, 'header': header}
        except Exception as e:
            self._dbg('playerContent extract error:', e)

        # 交给壳解析
        self._dbg('no direct link, parse=1:', play_url)
        return {'parse': 1, 'url': play_url, 'jx': 0, 'header': header}

    # ============================================================================
    # 十、searchContent: 搜索
    # ============================================================================
    def searchContent(self, key, quick='', pg=1):
        """按照关键词和实际页码读取原站搜索结果。"""
        page = int(pg) if pg else 1
        result = {'list': [], 'page': page, 'pagecount': 1, 'limit': 20, 'total': 0}
        if not key:
            return result

        try:
            resp = self._api_post('vod/search', {
                'wd': key,
                'page': page,
                'token': '',
            })
            if not resp or resp.get('code') != 0:
                self._dbg('vod/search error: code=%s msg=%s' % (resp.get('code', '?'), resp.get('msg', '')))
                return result

            videos = []
            for item in resp.get('list', []):
                videos.append({
                    'vod_id': str(item.get('vod_id', '')),
                    'vod_name': self.cleanText(item.get('vod_name', '')),
                    'type_name': self.cleanText(item.get('type_name', '')),
                    'vod_pic': item.get('vod_pic', '') or item.get('vod_pic_thumb', ''),
                    'vod_remarks': self.cleanText(item.get('vod_remarks', '')),
                })

            result['list'] = videos
            result['pagecount'] = int(resp.get('pagecount', 1) or 1)
            result['limit'] = int(resp.get('limit', len(videos)) or len(videos))
            result['total'] = int(resp.get('total', len(videos)) or len(videos))
        except Exception as e:
            self._dbg('searchContent error:', e)

        return result

    def searchContentPage(self, key, quick='', page='1'):
        """兼容 CatVod 的独立搜索分页入口。"""
        return self.searchContent(key, quick, int(page) if page else 1)

    # ============================================================================
    # 十一、可选方法
    # ============================================================================
    def isVideoFormat(self, url):
        """保留格式检查入口，最终格式由真实媒体结果判断。"""
        return False

    def manualVideoCheck(self):
        """保留手动检查入口，不触发媒体下载。"""
        return False


# ================================================================================
# 本地自检
# ================================================================================
