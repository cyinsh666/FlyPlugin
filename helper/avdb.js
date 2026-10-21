/**
 * avdb - 蜂蜜影视 CatVod JS Spider
 * API: https://avdbapi.com/api.php/provide/vod
 */
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/604.1.14 (KHTML, like Gecko)';
const API = 'https://avdbapi.com/api.php/provide/vod';

const HEADERS = {
  'User-Agent': UA,
  'Accept': 'application/json, text/plain, */*',
};

let siteKey = '';
let siteType = 0;

/** 使用源请求头读取文本响应，兼容同步 CatVod 请求。 */
function httpGet(url, extraHeaders) {
  let body = '';
  const headers = Object.assign({}, HEADERS, extraHeaders || {});
  try {
    const res = req(url, {
      method: 'get',
      headers: headers,
      timeout: 20000,
    });
    if (typeof res === 'string') {
      body = res;
    } else if (res) {
      body = res.content || res.body || res.data || '';
      if (typeof body !== 'string') body = JSON.stringify(body);
    }
  } catch (e1) {
    try {
      if (typeof request === 'function') {
        body = request(url, { headers: headers });
      }
    } catch (e2) {}
  }
  return body || '';
}

/** 解析明确的 JSON 内容，保留源协议返回结构。 */
function parseJson(text) {
  try {
    if (typeof text === 'object' && text !== null) return text;
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

/** 保留原站分类字段；分类页上下文仅补缺失，不用于猜测搜索类别。 */
function mapVod(e, categoryHint) {
  return {
    vod_id: String(e.id || e.vod_id || ''),
    type_id: String(e.type_id || e.category_id || (e.category && e.category.id) || categoryHint || ''),
    type_name: String(e.type_name || (e.category && e.category.name) || ''),
    vod_name: e.name || e.vod_name || '',
    vod_pic: e.poster_url || e.thumb_url || e.vod_pic || '',
    vod_remarks: e.tag || e.quality || e.type_name || '',
  };
}

/** 保存当前源的初始化参数与会话。 */
async function init(cfg) {
  siteKey = (cfg && cfg.skey) || '';
  siteType = (cfg && cfg.stype) || 0;
}

/** 读取源首页分类。 */
async function home(filter) {
  try {
    const raw = httpGet(API);
    const data = parseJson(raw);
    const classes = [];
    if (data && data.class && data.class.length) {
      for (let i = 0; i < data.class.length; i++) {
        const c = data.class[i];
        classes.push({
          type_id: String(c.type_id),
          type_name: c.type_name || String(c.type_id),
        });
      }
    }
    if (classes.length === 0) {
      // 兜底固定分类
      const fallback = [
        [1, 'Censored'],
        [2, 'Uncensored'],
        [3, 'Uncensored Leaked'],
        [4, 'Amateur'],
        [5, 'Chinese AV'],
        [6, 'Hentai'],
        [7, 'English subtitle'],
      ];
      for (let i = 0; i < fallback.length; i++) {
        classes.push({ type_id: String(fallback[i][0]), type_name: fallback[i][1] });
      }
    }
    return JSON.stringify({ class: classes, filters: {} });
  } catch (e) {
    return JSON.stringify({ class: [], filters: {} });
  }
}

/** 读取首页作品摘要，不提前解析播放。 */
async function homeVod() {
  try {
    const raw = httpGet(API + '?ac=detail&pg=1');
    const data = parseJson(raw);
    const list = [];
    if (data && data.list) {
      for (let i = 0; i < data.list.length && i < 24; i++) {
        list.push(mapVod(data.list[i]));
      }
    }
    return JSON.stringify({ list: list });
  } catch (e) {
    return JSON.stringify({ list: [] });
  }
}

/** 读取指定真实分类及源分页。 */
async function category(tid, pg, filter, extend) {
  try {
    const page = parseInt(pg) || 1;
    const url = API + '?t=' + encodeURIComponent(tid) + '&ac=detail&pg=' + page;
    const raw = httpGet(url);
    const data = parseJson(raw);
    const list = [];
    if (data && data.list) {
      for (let i = 0; i < data.list.length; i++) {
        list.push(mapVod(data.list[i], String(tid)));
      }
    }
    const pagecount = (data && data.pagecount) ? parseInt(data.pagecount) : (list.length > 0 ? page + 1 : page);
    const total = (data && data.total) ? parseInt(data.total) : 0;
    return JSON.stringify({
      page: page,
      pagecount: pagecount || 1,
      limit: 20,
      total: total || list.length,
      list: list,
    });
  } catch (e) {
    return JSON.stringify({
      page: 1,
      pagecount: 1,
      limit: 20,
      total: 0,
      list: [],
    });
  }
}

/** 读取所选作品详情与真实播放线路。 */
async function detail(id) {
  try {
    const vid = String(id);
    const url = API + '?ac=detail&ids=' + encodeURIComponent(vid);
    const raw = httpGet(url);
    const data = parseJson(raw);
    if (!data || !data.list || !data.list.length) {
      return JSON.stringify({
        list: [{
          vod_id: vid,
          vod_name: '无数据',
          vod_pic: '',
          vod_content: '',
          vod_play_from: '默认',
          vod_play_url: '正片$',
        }],
      });
    }
    const e = data.list[0];
    let playFrom = 'VIP';
    let embed = '';
    try {
      if (e.episodes) {
        playFrom = e.episodes.server_name || 'VIP';
        if (e.episodes.server_data && e.episodes.server_data.Full) {
          embed = e.episodes.server_data.Full.link_embed || '';
        }
        // 兼容多个 server_data key
        if (!embed && e.episodes.server_data) {
          const keys = Object.keys(e.episodes.server_data);
          for (let i = 0; i < keys.length; i++) {
            const node = e.episodes.server_data[keys[i]];
            if (node && node.link_embed) {
              embed = node.link_embed;
              break;
            }
          }
        }
      }
    } catch (err) {}

    // 也可能直接是 vod_play_url 风格
    if (!embed && e.vod_play_url) {
      return JSON.stringify({
        list: [{
          ...mapVod(e),
          vod_id: vid,
          vod_name: e.name || e.vod_name || '',
          vod_pic: e.poster_url || e.vod_pic || '',
          vod_content: e.description || e.vod_content || '',
          vod_actor: (e.actor && e.actor.join) ? e.actor.join(',') : (e.vod_actor || ''),
          vod_remarks: e.tag || '',
          vod_play_from: e.vod_play_from || playFrom,
          vod_play_url: e.vod_play_url,
        }],
      });
    }

    return JSON.stringify({
      list: [{
        ...mapVod(e),
        vod_id: vid,
        vod_name: e.name || e.vod_name || '',
        vod_pic: e.poster_url || e.thumb_url || '',
        vod_content: e.description || '',
        vod_actor: (e.actor && e.actor.join) ? e.actor.join(',') : '',
        vod_year: e.year || '',
        vod_remarks: e.tag || e.time || '',
        vod_play_from: playFrom,
        vod_play_url: '正片$' + (embed || ''),
      }],
    });
  } catch (e) {
    return JSON.stringify({
      list: [{
        vod_id: id,
        vod_name: '详情失败',
        vod_pic: '',
        vod_content: String(e),
        vod_play_from: '默认',
        vod_play_url: '正片$',
      }],
    });
  }
}

/** 读取所选线路和分集的真实播放地址。 */
async function play(flag, id, flags) {
  try {
    let playUrl = id || '';
    // 已是 m3u8 / mp4
    if (/\.m3u8|\.mp4/i.test(playUrl)) {
      return JSON.stringify({
        parse: 0,
        jx: 0,
        url: playUrl,
        header: {
          'User-Agent': UA,
          'Referer': 'https://avdbapi.com/',
        },
      });
    }

    // embed 页提取 m3u8（upload18 PLAYER_CONFIG）
    if (playUrl.indexOf('http') === 0) {
      const html = httpGet(playUrl, {
        'User-Agent': UA,
        'Referer': 'https://avdbapi.com/',
      });
      let m = html.match(/"m3u8"\s*:\s*"([^"]+)"/);
      if (m && m[1]) {
        let m3u8 = m[1]
          .replace(/\\\//g, '/')
          .replace(/\\u([0-9a-fA-F]{4})/g, function (_, h) {
            return String.fromCharCode(parseInt(h, 16));
          });
        return JSON.stringify({
          parse: 0,
          jx: 0,
          url: m3u8,
          header: {
            'User-Agent': UA,
            'Referer': playUrl,
          },
        });
      }
      // 其它页面里的 m3u8
      m = html.match(/https?:\\\/\\\/[^"']+\.m3u8[^"']*/);
      if (m) {
        const u = m[0].replace(/\\\//g, '/');
        return JSON.stringify({
          parse: 0,
          jx: 0,
          url: u,
          header: { 'User-Agent': UA, 'Referer': playUrl },
        });
      }
      m = html.match(/https?:\/\/[^"'\s<>]+?\.m3u8[^"'\s<>]*/i);
      if (m) {
        return JSON.stringify({
          parse: 0,
          jx: 0,
          url: m[0],
          header: { 'User-Agent': UA, 'Referer': playUrl },
        });
      }
      // 嗅探
      return JSON.stringify({
        parse: 1,
        jx: 0,
        url: playUrl,
        header: { 'User-Agent': UA, 'Referer': 'https://avdbapi.com/' },
      });
    }

    return JSON.stringify({
      parse: 0,
      jx: 0,
      url: playUrl,
      header: { 'User-Agent': UA },
    });
  } catch (e) {
    return JSON.stringify({
      parse: 0,
      jx: 0,
      url: id,
      header: { 'User-Agent': UA },
    });
  }
}

/** 按关键字读取源搜索结果。 */
async function search(wd, quick, pg) {
  try {
    const page = parseInt(pg) || 1;
    const url = API + '?ac=detail&wd=' + encodeURIComponent(wd || '') + '&pg=' + page;
    const raw = httpGet(url);
    const data = parseJson(raw);
    const list = [];
    if (data && data.list) {
      for (let i = 0; i < data.list.length; i++) {
        list.push(mapVod(data.list[i]));
      }
    }
    const pagecount = (data && data.pagecount) ? parseInt(data.pagecount) : 1;
    return JSON.stringify({
      page: page,
      pagecount: pagecount || 1,
      list: list,
    });
  } catch (e) {
    return JSON.stringify({ list: [] });
  }
}

/** 导出标准 CatVod 内容源接口。 */
export function __jsEvalReturn() {
  return {
    init: init,
    home: home,
    homeVod: homeVod,
    category: category,
    detail: detail,
    play: play,
    search: search,
  };
}

// 关键变量：插件名称由宿主静态读取，不依赖源执行或上传文件路径。
const display_name = 'avdb';
