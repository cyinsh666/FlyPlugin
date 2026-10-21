// YesPorn - 客户端单文件源（纯正则解析，无 cheerio 依赖）
// 站点：https://cn.yesporn.ws/  入口：/enter
const HOST = 'https://cn.yesporn.ws';
const UA = 'Mozilla/5.0 (Linux; Android 14; 22127RK46C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';

/** 记录源内调试事件；云助手宿主不会输出日志正文。 */
function mylog(...args) {
    console.log('[YesPorn]', ...args);
}

/** 把原站相对路径展开为真实站内地址。 */
function full(u) {
    if (!u) return '';
    u = String(u);
    return u.indexOf('http') === 0 ? u : HOST + (u[0] === '/' ? '' : '/') + u;
}

/** 按源要求发送页面或表单请求。 */
async function myFetch(url, method, body) {
    try {
        var opt = { method: method || 'get', headers: { 'User-Agent': UA, 'Referer': HOST + '/', 'X-Requested-With': 'XMLHttpRequest' } };
        if (body) { opt.body = body; opt.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'; }
        let res = await req(url, opt);
        return (res && typeof res === 'object') ? res.content : res;
    } catch (err) {
        mylog('myfetch err', err);
        return '';
    }
}

/** 保存当前源的初始化参数与会话。 */
async function init(cfg) {
    mylog('Spider Init Done');
}

// 关键变量：/enter 是原站实际视频目录；排序方式及厂商筛选不冒充作品类别。
var CLASSES = [{ type_id: 'enter', type_name: '全部视频' }];

/** 读取源首页分类。 */
async function home(filter) {
    try {
        return JSON.stringify({ class: CLASSES, filters: {} });
    } catch (e) {
        mylog('home err', e.message);
        return JSON.stringify({ class: [], filters: {} });
    }
}

/** 读取首页作品摘要，不提前解析播放。 */
async function homeVod() {
    try {
        var html = await myFetch(HOST + '/enter');
        return JSON.stringify({ list: parseList(html) });
    } catch (e) {
        mylog('homeVod err', e.message);
        return JSON.stringify({ list: [] });
    }
}

/** 读取当前源的分类与首页摘要。 */
async function homeContent(filter) {
    try {
        var html = await myFetch(HOST + '/enter');
        return JSON.stringify({ class: CLASSES, filters: {}, list: parseList(html) });
    } catch (e) {
        mylog('homeContent err', e.message);
        return JSON.stringify({ class: CLASSES, filters: {}, list: [] });
    }
}

// 纯正则解析列表
function parseList(html) {
    if (!html) return [];
    var list = [];
    var seen = {};
    var blocks = html.split('<div class="thumb');
    for (var i = 1; i < blocks.length; i++) {
        var b = blocks[i];
        var mh = b.match(/href="(https?:\/\/[^"]*\/video\/[^"]+)"/);
        if (!mh) continue;
        var href = mh[1];

        var title = '';
        var mt = b.match(/title="([^"]*)"/);
        if (mt) title = mt[1].trim();
        if (!title) {
            var ma = b.match(/alt="([^"]*)"/);
            if (ma) title = ma[1].trim();
        }
        if (!title) continue;

        var pic = '';
        var mp = b.match(/data-original="([^"]+)"/);
        if (mp) pic = mp[1];

        var remark = '';
        var mr = b.match(/class="time">([^<]+)</);
        if (mr) remark = mr[1].trim();

        var vid = href;
        if (seen[vid]) continue;
        seen[vid] = 1;

        list.push({
            type_id: 'enter',
            type_name: '全部视频',
            vod_id: vid,
            vod_name: title,
            vod_pic: pic,
            vod_remarks: remark
        });
    }
    return list;
}

/** 读取指定真实分类及源分页。 */
async function category(tid, pg, filter, extend) {
    try {
        pg = parseInt(pg) || 1;
        if (tid !== 'enter') throw new Error('此源只提供实际视频目录 enter');
        var sortBy = 'post_date';
        var body = 'block_id=list_videos_most_recent_videos&sort_by=' + sortBy + '&from=' + pg;
        mylog('category POST from=' + pg);
        var html = await myFetch(HOST + '/enter', 'post', body);
        var list = parseList(html);
        return JSON.stringify({ list, page: pg, ...(list.length === 0 ? { pagecount: pg } : {}) });
    } catch (e) {
        mylog('category err', e.message);
        return JSON.stringify({ list: [], pagecount: 1 });
    }
}
/** 读取所选作品详情与真实播放线路。 */
async function detail(id) {
    try {
        var pageUrl = full(id);
        mylog('detail url', pageUrl);
        var html = await myFetch(pageUrl);
        if (!html) return JSON.stringify({ list: [] });

        var name = '';
        var mh = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/);
        if (mh) name = mh[1].replace(/<[^>]+>/g, '').trim();
        if (!name) {
            var mo = html.match(/og:title"\s+content="([^"]*)"/);
            if (mo) name = mo[1];
        }
        if (!name) {
            var mt = html.match(/<title>([\s\S]*?)<\/title>/);
            if (mt) name = mt[1].trim();
        }

        var pic = '';
        var mp = html.match(/og:image"\s+content="([^"]*)"/);
        if (mp) pic = mp[1];

        var content = '';
        var mc = html.match(/id="resume"[^>]*>([\s\S]*?)<\/span>/);
        if (mc) content = mc[1].replace(/<[^>]+>/g, '').trim();

        var vod = {
            type_id: 'enter',
            type_name: '全部视频',
            vod_id: pageUrl,
            vod_name: name,
            vod_pic: pic,
            vod_content: content,
            vod_remarks: '',
            vod_play_from: 'YesPorn',
            vod_play_url: '播放$' + pageUrl
        };
        return JSON.stringify({ list: [vod] });
    } catch (e) {
        mylog('detail err', e.message);
        return JSON.stringify({ list: [] });
    }
}

/** 按关键字读取源搜索结果。 */
async function search(wd, quick, pg) {
    try {
        if (Number(pg || 1) > 1) return JSON.stringify({ list: [], pagecount: 1, page: Number(pg) });
        var url = HOST + '/search/?q=' + encodeURIComponent(wd);
        mylog('search url', url);
        var html = await myFetch(url);
        return JSON.stringify({ list: parseList(html), pagecount: 1, page: 1 });
    } catch (e) {
        mylog('search err', e.message);
        return JSON.stringify({ list: [], pagecount: 1 });
    }
}

/** 检查当前地址是否明确指向媒体格式。 */
function isDirectVideoUrl(url) {
    return ['m3u', 'mp4'].some(function (x) { return (url + '').includes(x); });
}

/** 读取所选线路和分集的真实播放地址。 */
async function play(flag, id, flags) {
    mylog('play id:', id);
    try {
        var pageUrl = full(id);
        var m = pageUrl.match(/\/video\/(\d+)/);
        var vid = m ? m[1] : '';
        if (!vid) {
            return JSON.stringify({ parse: 1, url: pageUrl, header: { 'User-Agent': UA, 'Referer': HOST + '/' } });
        }
        // 走下载页拿带 download=true 的真直链（绕过 kt_player 校验）
        var dlPage = HOST + '/view_video_download.php?id=' + vid + '&format=720';
        var html = await myFetch(dlPage);
        var real = extractDownloadUrl(html);
        if (!real) {
            html = await myFetch(HOST + '/view_video_download.php?id=' + vid + '&format=480');
            real = extractDownloadUrl(html);
        }
        if (real) {
            mylog('命中下载直链', real);
            return JSON.stringify({ parse: 0, url: real, header: { 'User-Agent': UA, 'Referer': dlPage } });
        }
        // 兜底：转嗅探 embed
        return JSON.stringify({ parse: 1, url: HOST + '/embed/' + vid + '/', header: { 'User-Agent': UA, 'Referer': pageUrl } });
    } catch (e) {
        mylog('play失败:', e.message);
        return JSON.stringify({ parse: 1, url: full(id), header: { 'User-Agent': UA, 'Referer': HOST + '/' } });
    }
}

// 从下载页提取带 download=true 的真直链
function extractDownloadUrl(html) {
    if (!html) return '';
    var m = html.match(/(https?:\/\/[^"'\s<>]+\/get_file\/[^"'\s<>]+\.mp4[^"'\s<>]*)/);
    if (m && m[1]) return m[1].replace(/&amp;/g, '&');
    return '';
}function extractVideoUrl(html) {
    if (!html) return '';
    var m = html.match(/video_url\s*:\s*'([^']+)'/);
    if (!m) m = html.match(/video_url\s*:\s*"([^"]+)"/);
    if (m && m[1]) {
        var u = m[1].replace(/^function\/\d+\//, '');
        if (u.indexOf('http') === 0) return u;
    }
    m = html.match(/(https?:\/\/[^"'\s]+\/get_file\/[^"'\s]+\.mp4[^"'\s]*)/);
    if (m && m[1]) return m[1];
    return '';
}

export default {
    init,
    home,
    homeVod,
    category,
    detail,
    search,
    play
};

// 关键变量：插件名称由宿主静态读取，不依赖源执行或上传文件路径。
const display_name = 'YesPorn';
