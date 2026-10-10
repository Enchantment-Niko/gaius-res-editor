#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, sys, gzip, json, queue, base64, struct, hashlib, threading, traceback, binascii
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, scrolledtext
from collections import defaultdict

BUILD_TAG = '| 构建版本 2026/10/10 23:48'

# ============ GAIUS ============
GAIUS_MAGIC = b'GAIUSVP1'
GAIUS_HEADER = 12
GAIUS_VANILLA_KEYS = (
    '"vanilla":[', "'vanilla':[", 'vanilla:[',
    '"vanilla": [', "'vanilla': [", 'vanilla: [',
    '"vanilla" :[', '"vanilla" : [', 'vanilla\t:[', 'vanilla\t: [',
)
GAIUS_BOOT_REPLACEMENTS = [
    ('"ONLINE SESSION"', '"在线会话"', False),
    ('"PORTABLE HTML"', '"便携 HTML"', False),
    ('"BROWSER CLIENT"', '"浏览器客户端"', False),
    (r'VERSION (\d+\.\d+\.\d+)', r'版本 \1', True),
    ('Gaius Client</strong> | independent browser software',
     'Gaius 客户端</strong> | 独立浏览器软件', False),
    ('HTML runtime | local storage enabled', 'HTML 运行时 | 本地存储已启用', False),
    ('Retry startup', '重试启动', False),
    ('Show diagnostics', '显示诊断', False),
    ('Hide diagnostics', '隐藏诊断', False),
    ('BROWSER CLIENT', '浏览器客户端', False),
    ('Starting Gaius Client 26.3...', '正在启动 Gaius 客户端 26.3...', False),
    ('"Loading persistent browser storage..."', '"正在加载浏览器持久化存储..."', False),
    ('"Browser storage is ready ("', '"浏览器存储已就绪 ("', False),
    ('"); loading classes.js..."', '"); 正在加载 classes.js..."', False),
    ('"classes.js loaded; calling net.minecraft.client.main.Main.main(args)...\\n"',
     '"classes.js 已加载; 正在调用 net.minecraft.client.main.Main.main(args)...\\n"', False),
    ('"Browser runtime error:"', '"浏览器运行时错误:"', False),
    ('"Unhandled browser promise rejection:"', '"未处理的浏览器 Promise 拒绝:"', False),
    ('"Gaius Client failed to start:"', '"Gaius 客户端启动失败:"', False),
    ('"0% Initializing..."', '"0% 初始化中..."', False),
]

GAIUS_INJECT_TEMPLATE = '''
<script>
(function gaiusIndexedDbInjector() {
  const INJECT_FILES = __INJECT_FILES__;
  const TARGET_PREFIX = "__TARGET_PREFIX__";
  function log(message) {
    const now = new Date();
    const pad = (n, w) => String(n).padStart(w, "0");
    const stamp = pad(now.getHours(), 2) + ":" + pad(now.getMinutes(), 2)
      + ":" + pad(now.getSeconds(), 2) + "." + pad(now.getMilliseconds(), 3);
    console.log("[" + stamp + "] [INFO] " + message);
  }
  function b64ToBytes(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }
  async function writeAll() {
    try { await (window.__gaiusFsReady || Promise.resolve()); } catch (_) {}
    for (const entry of INJECT_FILES) {
      const targetPath = TARGET_PREFIX + entry.path;
      const bytes = b64ToBytes(entry.data);
      if (typeof window.__gaiusFsPutBytes === "function") {
        if (window.__gaiusFsPutBytes(targetPath, bytes)) { log("wrote " + targetPath + " via __gaiusFsPutBytes"); continue; }
      }
      if (typeof window.__gaiusFsPut === "function") {
        if (window.__gaiusFsPut(targetPath, entry.data)) { log("wrote " + targetPath + " via __gaiusFsPut"); continue; }
      }
      try {
        const dbName = window.__gaiusStorageDatabaseName || "gaius-fs-v2-26.3";
        const storeName = "files";
        await new Promise((resolve) => {
          const openReq = indexedDB.open(dbName);
          openReq.onsuccess = () => {
            const db = openReq.result;
            const tx = db.transaction(storeName, "readwrite");
            tx.objectStore(storeName).put({ path: targetPath, value: bytes, updatedAt: Date.now() });
            tx.oncomplete = () => { log("wrote " + targetPath + " via IndexedDB"); db.close(); resolve(); };
            tx.onerror = () => { log("IndexedDB transaction failed: " + tx.error); resolve(); };
          };
          openReq.onerror = () => { log("IndexedDB open failed: " + openReq.error); resolve(); };
        });
      } catch (e) { log("exception: " + e); }
    }
  }
  if (document.readyState === "complete") setTimeout(writeAll, 2000);
  else window.addEventListener("load", () => setTimeout(writeAll, 2000));
})();
</script>
'''


def _find_matching_bracket(text, open_pos):
    depth = 0; in_str = False; escape = False; i = open_pos; n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if escape: escape = False
            elif c == '\\': escape = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == '[': depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0: return i
        i += 1
    raise ValueError("找不到匹配的 ']'")


def gaius_extract_vanilla(html, debug=False):
    for key in GAIUS_VANILLA_KEYS:
        pos = html.find(key)
        if pos < 0: continue
        open_pos = html.index('[', pos)
        close_pos = _find_matching_bracket(html, open_pos)
        body = html[open_pos + 1:close_pos]
        chunks = re.findall(r'"([^"]*)"', body)
        if debug:
            print(f"[DBG] key={key!r} chunks={len(chunks)}")
        return chunks, open_pos, close_pos
    return None


def gaius_replace_vanilla(html, chunks):
    found = gaius_extract_vanilla(html)
    if not found: raise ValueError("HTML 里找不到 vanilla 数组")
    _, open_pos, close_pos = found
    new_array = '[\n' + ',\n'.join('  "' + c + '"' for c in chunks) + '\n]'
    return html[:open_pos] + new_array + html[close_pos + 1:]


class GaiusPack:
    def __init__(self, index=None, data=None):
        self.index = index or {}
        self.data = bytearray(data or b'')

    @classmethod
    def from_raw(cls, raw):
        if raw[:8] != GAIUS_MAGIC: raise ValueError("不是 GAIUSVP1 包")
        index_len = struct.unpack_from('<I', raw, 8)[0]
        start = GAIUS_HEADER; end = start + index_len
        if end > len(raw): raise ValueError("索引越界")
        index = json.loads(raw[start:end].decode('utf-8'))
        data = raw[end:]
        clean = {}
        for path, rng in index.items():
            if not isinstance(rng, list) or len(rng) != 2: raise ValueError(f"索引项非法: {path}")
            off, length = int(rng[0]), int(rng[1])
            if off < 0 or length < 0 or off + length > len(data): raise ValueError(f"索引越界: {path}")
            clean[path] = [off, length]
        return cls(clean, bytearray(data))

    def to_raw(self, compact=True):
        if compact: self._compact()
        idx = json.dumps(self.index, separators=(',', ':')).encode('utf-8')
        return GAIUS_MAGIC + struct.pack('<I', len(idx)) + idx + bytes(self.data)

    def to_gzip(self, level=9, compact=True):
        return gzip.compress(self.to_raw(compact=compact), compresslevel=level)

    def _compact(self):
        new_data = bytearray(); new_index = {}
        for path in sorted(self.index.keys()):
            off, length = self.index[path]
            new_off = len(new_data); new_data += self.data[off:off + length]
            new_index[path] = [new_off, length]
        self.data = new_data; self.index = new_index

    def paths(self): return list(self.index.keys())
    def has(self, p): return p in self.index
    def read(self, p):
        off, length = self.index[p]; return bytes(self.data[off:off + length])

    def add(self, p, c):
        if p in self.index: return self.replace(p, c)
        off = len(self.data); self.data += c; self.index[p] = [off, len(c)]
        return 'added'

    def replace(self, p, c):
        if p not in self.index: return self.add(p, c)
        off, old_len = self.index[p]
        if len(c) <= old_len:
            self.data[off:off + len(c)] = c
            if len(c) < old_len:
                self.data[off + len(c):off + old_len] = b'\x00' * (old_len - len(c))
            self.index[p] = [off, len(c)]
            return 'replaced-inplace'
        new_off = len(self.data); self.data += c; self.index[p] = [new_off, len(c)]
        return 'replaced-appended'

    def delete(self, p):
        if p not in self.index: return False
        del self.index[p]; return True

    def rename(self, o, n):
        if o not in self.index or o == n: return False
        if n in self.index: raise ValueError(f"目标路径已存在: {n}")
        self.index[n] = self.index.pop(o); return True

    def is_modified(self): return False
    def stats(self): return {'files': len(self.index), 'payload_bytes': len(self.data)}


def gaius_load_html(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()
    found = gaius_extract_vanilla(html, debug=True)
    if not found: raise ValueError("不是 Gaius HTML（找不到 vanilla 数组）")
    chunks, _, _ = found
    raw = b''.join(base64.b64decode(c) for c in chunks)
    decompressed = gzip.decompress(raw)
    return GaiusPack.from_raw(decompressed)


def gaius_build_html(src_html, pack, out_path, localize=False, inject_files=None,
                     target_prefix='/gaius/'):
    with open(src_html, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()
    gz = pack.to_gzip()
    chunks = [base64.b64encode(gz[i:i + 8 * 1024 * 1024]).decode('ascii')
              for i in range(0, len(gz), 8 * 1024 * 1024)]
    html = gaius_replace_vanilla(html, chunks)
    if localize:
        for old, new, is_re in GAIUS_BOOT_REPLACEMENTS:
            if is_re:
                html = re.sub(old, new, html)
            else:
                html = html.replace(old, new)
    if inject_files:
        payload = [{"path": p.lstrip('/'), "data": base64.b64encode(d).decode('ascii')}
                   for p, d in inject_files]
        snippet = (GAIUS_INJECT_TEMPLATE
                   .replace('__INJECT_FILES__', json.dumps(payload))
                   .replace('__TARGET_PREFIX__', target_prefix))
        if "gaiusIndexedDbInjector" in html:
            raise ValueError("这个 HTML 已经注入过 IndexedDB 注入器了")
        idx = html.rfind("</body>")
        html = (html[:idx] + snippet + html[idx:]) if idx >= 0 else (html + snippet)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


# ============ EAGLER26 ============
E_CHUNK_OPEN = b'<script type="application/x-eagler26-chunk"'
E_CLOSE = b'</script>'
E_META_ID = b'id="eagler26-offline-meta"'
E_BUILD_ID = b'id="eagler26-build"'
E_CHUNK = 8 * 1024 * 1024
E_DEF_ESC = 0x3d
E_DEF_BASE = 0x40
E_DEF_UNSAFE = [0x00, 0x0d, 0x3c, 0x3d] + list(range(0x80, 0xa0))
E_A = '__ASSETS__/'
E_U = '__AUDIO__/'
E_TAKE = b'<script>__eaglerOffline.take()</script>\n'
E_TAKE_RE = re.compile(rb'\s*<script>\s*__?eaglerOffline\.take\(\)\s*</script>')
E_AUDIO_GROUPS = [
    ('sfx', 'audio/sfx-original.idx', 'audio/sfx-original.bin'),
    ('music', 'audio/music-original.idx', 'audio/music-original.bin'),
    ('records', 'audio/records-original.idx', 'audio/records-original.bin'),
    ('sfx-opt', 'audio/sfx-optimized.idx', 'audio/sfx-optimized.bin'),
]


def eagler_sum(data):
    s1 = s2 = 0
    for c in data:
        s1 = (s1 + c) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
    return ((s1 & 0xffff) ^ ((s2 << 16) & 0xFFFFFFFF)) & 0xFFFFFFFF


class ECodec:
    def __init__(self, esc, base, unsafe):
        self.esc = int(esc); self.base = int(base); self.unsafe = list(unsafe)
        self._rev = {b: chr(self.base + i) for i, b in enumerate(self.unsafe)}

    def decode(self, text):
        raw = text.encode('windows-1252', 'replace')
        if self.esc not in raw: return raw
        out = bytearray(); i = 0; n = len(raw)
        while True:
            j = raw.find(self.esc, i)
            if j < 0:
                out += raw[i:]; break
            out += raw[i:j]
            if j + 1 >= n: break
            k = raw[j + 1] - self.base
            out.append(self.unsafe[k] if 0 <= k < len(self.unsafe) else raw[j + 1])
            i = j + 2
        return bytes(out)

    def encode(self, data):
        out = bytearray()
        for b in data:
            if b in self._rev:
                out.append(self.esc); out.append(ord(self._rev[b]))
            else: out.append(b)
        return out.decode('windows-1252', 'replace')


class EAssets:
    def __init__(self):
        self.ranges = {}; self.blob = b''
        self.dirty = {}; self.deleted = set(); self.new = {}; self.order = []

    @classmethod
    def from_raw(cls, idx_gz, bin_gz):
        self = cls()
        self.ranges = json.loads(gzip.decompress(idx_gz).decode('utf-8'))['ranges']
        self.blob = gzip.decompress(bin_gz)
        self.order = list(self.ranges.keys())
        return self

    def read(self, p):
        if p in self.new: return self.new[p]
        if p in self.deleted: raise KeyError(p)
        if p in self.dirty: return self.dirty[p]
        off, ln = self.ranges[p]; return self.blob[off:off + ln]

    def write(self, p, c): self.dirty[p] = c; self.deleted.discard(p)

    def delete(self, p):
        if p in self.dirty: del self.dirty[p]; return True
        if p in self.new: del self.new[p]; return True
        if p in self.ranges: self.deleted.add(p); return True
        return False

    def add(self, p, c): self.new[p] = c; self.dirty[p] = c; return 'added'

    def rename(self, o, n):
        if n in self.ranges or n in self.new: raise ValueError(f'目标已存在: {n}')
        d = self.read(o); self.delete(o); self.add(n, d); return True

    def all_paths(self):
        s = set(self.ranges) | set(self.new) - self.deleted
        return [p for p in self.order if p in s] + sorted(s - set(self.order))

    def is_modified(self): return bool(self.dirty or self.deleted or self.new)

    def rebuild(self):
        manifest = {'assets': {}}; ranges = {}; buf = bytearray()
        for p in self.all_paths():
            d = self.read(p); off = len(buf); buf += d
            manifest['assets'][p] = './' + p; ranges[p] = [off, len(d)]
        j = json.dumps({'manifest': manifest, 'ranges': ranges},
                       separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return gzip.compress(j, 9), gzip.compress(bytes(buf), 9)


class EAudio:
    def __init__(self):
        self.ranges = defaultdict(dict); self.blobs = {}
        self.dirty = defaultdict(dict); self.deleted = defaultdict(set); self.new = defaultdict(dict)
        self.order = defaultdict(list); self.idx_names = {}; self.bin_names = {}

    @classmethod
    def from_raw(cls, res):
        self = cls()
        for label, iname, bname in E_AUDIO_GROUPS:
            if iname not in res or bname not in res: continue
            try:
                gz = res[iname]
                try: idx = json.loads(gzip.decompress(gz).decode('utf-8'))
                except Exception: idx = json.loads(gz.decode('utf-8'))
                r = idx.get('ranges', idx)
                if not isinstance(r, dict) or not r: continue
                try: blob = gzip.decompress(res[bname])
                except Exception: blob = res[bname]
                self.ranges[label] = r; self.blobs[label] = blob
                self.order[label] = list(r.keys())
                self.idx_names[label] = iname; self.bin_names[label] = bname
            except Exception: pass
        return self

    def read(self, l, p):
        if p in self.new[l]: return self.new[l][p]
        if p in self.deleted[l]: raise KeyError(p)
        if p in self.dirty[l]: return self.dirty[l][p]
        r = self.ranges[l].get(p)
        if not r: raise KeyError(p)
        return self.blobs[l][r[0]:r[0] + r[1]]

    def write(self, l, p, c): self.dirty[l][p] = c; self.deleted[l].discard(p)

    def delete(self, l, p):
        if p in self.dirty[l]: del self.dirty[l][p]; return True
        if p in self.new[l]: del self.new[l][p]; return True
        if p in self.ranges[l]: self.deleted[l].add(p); return True
        return False

    def add(self, l, p, c): self.new[l][p] = c; self.dirty[l][p] = c; return 'added'

    def all_paths(self, l):
        s = set(self.ranges[l]) | set(self.new[l]) - self.deleted[l]
        return [p for p in self.order[l] if p in s] + sorted(s - set(self.order[l]))

    def is_modified(self): return any(self.dirty[k] or self.deleted[k] or self.new[k] for k in self.ranges)

    def rebuild(self):
        out = {}
        for l in self.ranges:
            manifest = {}; ranges = {}; buf = bytearray()
            for p in self.all_paths(l):
                d = self.read(l, p); off = len(buf); buf += d
                manifest[p] = './' + p; ranges[p] = [off, len(d)]
            j = json.dumps({'manifest': manifest, 'ranges': ranges},
                           separators=(',', ':'), ensure_ascii=False).encode('utf-8')
            out[l] = (gzip.compress(j, 9), gzip.compress(bytes(buf), 9))
        return out


class EaglerPack:
    def __init__(self):
        self.head = b''; self.tail = b''
        self.meta = {}; self.build = {}
        self.codec = None
        self.res = {}; self.order = []
        self.assets = None; self.audio = None
        self.src_path = None

    @classmethod
    def from_html(cls, path, cancel):
        self = cls(); self.src_path = path
        with open(path, 'rb') as f: html = f.read()
        m = re.search(re.escape(E_META_ID) + rb'[^>]*>(.*?)' + re.escape(E_CLOSE), html, re.S)
        if not m: raise ValueError('找不到 eagler26-offline-meta')
        self.meta = json.loads(m.group(1).decode('utf-8'))
        enc = self.meta.get('encoding', {})
        self.codec = ECodec(enc.get('escape', E_DEF_ESC), enc.get('base', E_DEF_BASE),
                            enc.get('unsafe', E_DEF_UNSAFE))
        m2 = re.search(re.escape(E_BUILD_ID) + rb'[^>]*>(.*?)' + re.escape(E_CLOSE), html, re.S)
        if m2:
            try: self.build = json.loads(m2.group(1).decode('utf-8'))
            except Exception: pass
        spans = []
        pat = re.compile(re.escape(E_CHUNK_OPEN) + rb'([^>]*)>(.*?)' + re.escape(E_CLOSE), re.S)
        for mm in pat.finditer(html):
            if cancel.is_set(): raise RuntimeError('取消')
            a = mm.group(1); b = mm.group(2)
            r = re.search(rb'data-r="([^"]+)"', a)
            l = re.search(rb'data-len="(\d+)"', a)
            s = re.search(rb'data-sum="(\d+)"', a)
            if not (r and l and s): continue
            spans.append({'path': r.group(1).decode('utf-8'), 'len': int(l.group(1)),
                          'sum': int(s.group(1)), 'body': b,
                          'start': mm.start(), 'end': mm.end()})
        if spans:
            self.head = html[:spans[0]['start']]
            last = spans[-1]['end']
            while True:
                m = E_TAKE_RE.match(html[last:])
                if m:
                    last += m.end()
                    if html[last:last + 1] == b'\n': last += 1
                else: break
            self.tail = html[last:]
        by = defaultdict(list); seen = []
        for sp in spans:
            if sp['path'] not in by: seen.append(sp['path'])
            by[sp['path']].append(sp)
        for path in seen:
            if cancel.is_set(): raise RuntimeError('取消')
            raw = bytearray()
            for p in by[path]:
                raw += self.codec.decode(p['body'].decode('windows-1252'))
            self.res[path] = bytes(raw); self.order.append(path)
        if 'assets/assets.idx' in self.res and 'assets/assets.bin' in self.res:
            try: self.assets = EAssets.from_raw(self.res['assets/assets.idx'], self.res['assets/assets.bin'])
            except Exception: self.assets = None
        try: self.audio = EAudio.from_raw(self.res)
        except Exception: self.audio = None
        return self

    def _update_meta(self):
        def fix(c):
            if not isinstance(c, dict): return
            for p, r in c.items():
                if not isinstance(r, dict) or p not in self.res: continue
                gz = self.res[p]
                if r.get('encoding') == 'gzip':
                    try: plain = gzip.decompress(gz)
                    except Exception: plain = gz
                else: plain = gz
                r['size'] = len(plain); r['stored'] = len(gz)
                r['sha256'] = hashlib.sha256(plain).hexdigest()
        fix(self.meta.get('resources', {}))
        fix(self.build)

    def _fix_head(self):
        def repl(head, id_bytes, obj):
            if not obj: return head
            new = (b'<script ' + id_bytes + b' type="application/json">'
                   + json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
                   + b'</script>')
            pat = re.compile(re.escape(id_bytes) + rb'[^>]*>.*?' + re.escape(E_CLOSE), re.S)
            m = pat.search(head)
            if not m: return head
            s = head.rfind(b'<script', 0, m.start())
            if s < 0: return head
            return head[:s] + new + head[m.end():]
        self.head = repl(self.head, E_META_ID, self.meta)
        self.head = repl(self.head, E_BUILD_ID, self.build)

    def to_html(self, out, log, cancel, chunk_size=E_CHUNK):
        if self.assets and self.assets.is_modified():
            log('[i] 重建 assets.idx/bin')
            i, b = self.assets.rebuild()
            self.res['assets/assets.idx'] = i; self.res['assets/assets.bin'] = b
        if self.audio and self.audio.is_modified():
            log('[i] 重建 audio')
            for label, (i, b) in self.audio.rebuild().items():
                if self.audio.idx_names.get(label):
                    self.res[self.audio.idx_names[label]] = i
                    self.res[self.audio.bin_names[label]] = b
        log('[i] 更新 meta/build')
        self._update_meta(); self._fix_head()
        order = [p for p in self.order if p in self.res]
        order += sorted(p for p in self.res if p not in order)
        total = 0
        with open(out, 'wb') as f:
            f.write(self.head)
            for path in order:
                if cancel.is_set(): raise RuntimeError('取消')
                raw = self.res[path]; off = 0
                while off < len(raw):
                    piece = raw[off:off + chunk_size]
                    s = eagler_sum(piece)
                    tag = (f'<script type="application/x-eagler26-chunk" '
                           f'data-r="{path}" data-len="{len(piece)}" data-sum="{s}">')
                    f.write(tag.encode('utf-8'))
                    f.write(self.codec.encode(piece).encode('windows-1252', 'replace'))
                    f.write(b'</script>'); f.write(E_TAKE)
                    off += len(piece); total += 1
            f.write(self.tail)
        log(f'[✓] 写出 {out} ({os.path.getsize(out)/1024/1024:.2f} MB)，{total} chunk')

    def read(self, v):
        if v.startswith(E_A):
            if not self.assets: raise KeyError(v)
            return self.assets.read(v[len(E_A):])
        if v.startswith(E_U):
            l, _, p = v[len(E_U):].partition('/')
            if not self.audio: raise KeyError(v)
            return self.audio.read(l, p)
        return self.res[v]

    def write(self, v, c):
        if v.startswith(E_A): self.assets.write(v[len(E_A):], c); return
        if v.startswith(E_U):
            l, _, p = v[len(E_U):].partition('/')
            self.audio.write(l, p, c); return
        self.res[v] = c
        if v not in self.order: self.order.append(v)

    def delete(self, v):
        if v.startswith(E_A): return self.assets.delete(v[len(E_A):])
        if v.startswith(E_U):
            l, _, p = v[len(E_U):].partition('/')
            return self.audio.delete(l, p)
        return self.res.pop(v, None) is not None

    def rename(self, o, n):
        if o.startswith(E_A) and n.startswith(E_A):
            return self.assets.rename(o[len(E_A):], n[len(E_A):])
        if o.startswith(E_U) and n.startswith(E_U):
            ol, _, op = o[len(E_U):].partition('/')
            nl, _, np_ = n[len(E_U):].partition('/')
            if ol != nl: raise ValueError('不允许跨类别重命名')
            d = self.audio.read(ol, op); self.audio.delete(ol, op); self.audio.add(nl, np_, d)
            return True
        if o not in self.res: return False
        if n in self.res: raise ValueError(f'目标已存在: {n}')
        self.res[n] = self.res.pop(o)
        if o in self.order: self.order[self.order.index(o)] = n
        else: self.order.append(n)
        return True

    def add(self, v, c):
        if v.startswith(E_A): return self.assets.add(v[len(E_A):], c)
        if v.startswith(E_U):
            l, _, p = v[len(E_U):].partition('/')
            return self.audio.add(l, p, c)
        if v in self.res: return 'replaced'
        self.res[v] = c; self.order.append(v)
        return 'added'

    def is_modified(self):
        return bool((self.assets and self.assets.is_modified()) or (self.audio and self.audio.is_modified()))

    def all_paths(self):
        return list(self.res.keys())

    def stats(self):
        return {'files': len(self.res),
                'payload_bytes': sum(len(v) for v in self.res.values()),
                'assets_files': len(self.assets.ranges) if self.assets else 0,
                'audio_files': sum(len(v) for v in self.audio.ranges.values()) if self.audio else 0}


# ============ 格式探测 ============
def detect_format(path):
    with open(path, 'rb') as f:
        head = f.read(1024 * 1024)
    if b'x-eagler26-chunk' in head or b'eagler26-offline-meta' in head:
        return 'eagler'
    if b'GAIUSVP1' in head:
        return 'gaius'
    if b'vanilla' in head:
        return 'gaius'
    return None


def load_any(path, log, cancel):
    fmt = detect_format(path)
    if fmt == 'eagler':
        log('[i] 格式: Eagler26 / GoldHQ 单文件')
        return EaglerPack.from_html(path, cancel), 'eagler'
    if fmt == 'gaius':
        log('[i] 格式: Gaius HTML / GAIUSVP1 裸包')
        if path.lower().endswith(('.html', '.htm')):
            return gaius_load_html(path), 'gaius'
        with open(path, 'rb') as f: raw = f.read()
        if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
        return GaiusPack.from_raw(raw), 'gaius'
    raise ValueError('无法识别格式（既不是 Eagler26 也不是 Gaius）')


# ============ Worker ============
class Worker:
    def __init__(self, q):
        self.q = q; self.qin = queue.Queue(); self.cancel = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn, *a, **k):
        self.cancel.clear(); self.qin.put((fn, a, k))

    def _loop(self):
        while True:
            fn, a, k = self.qin.get()
            try:
                k['cancel'] = self.cancel; fn(*a, **k)
            except Exception as e:
                self.q.put(('err', f'[✗] {e}'))
                self.q.put(('err', traceback.format_exc()))


# ============ UI 组件 ============
class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        vs = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = ttk.Frame(self.canvas)
        iid = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(iid, width=e.width))
        self._wheel = self._make_wheel()
        self._bind(self.inner)

    def _make_wheel(self):
        cv = self.canvas
        def handler(event):
            if getattr(event, 'num', 0) == 4: delta = -1
            elif getattr(event, 'num', 0) == 5: delta = 1
            else: delta = -1 if event.delta > 0 else 1
            cv.yview_scroll(delta * 3, 'units')
        return handler

    def _bind(self, w):
        w.bind('<MouseWheel>', self._wheel, add='+')
        w.bind('<Button-4>', self._wheel, add='+')
        w.bind('<Button-5>', self._wheel, add='+')
        for c in w.winfo_children(): self._bind(c)

    def refresh(self): self._bind(self.inner)


class TextIn(tk.Toplevel):
    def __init__(self, p, title, label, default=''):
        super().__init__(p); self.title(title); self.geometry('640x180')
        self.transient(p); self.grab_set(); self.result = None
        ttk.Label(self, text=label).pack(anchor='w', padx=15, pady=(15, 4))
        self.var = tk.StringVar(value=default)
        e = ttk.Entry(self, textvariable=self.var); e.pack(fill='x', padx=15, pady=4)
        e.focus_set(); e.select_range(0, 'end')
        b = ttk.Frame(self); b.pack(fill='x', padx=15, pady=10)
        ttk.Button(b, text='确定', command=self._ok).pack(side='right', padx=4)
        ttk.Button(b, text='取消', command=self.destroy).pack(side='right', padx=4)
        self.bind('<Return>', lambda e: self._ok())
        self.bind('<Escape>', lambda e: self.destroy())
        self.wait_window()

    def _ok(self): self.result = self.var.get(); self.destroy()


# ============ 主 GUI ============
class GUI:
    def __init__(self, root):
        self.root = root
        root.title(f'Gaius / Eagler26 资源编辑器 {BUILD_TAG}')
        root.geometry('1280x850'); root.minsize(960, 600)
        self._fonts()
        self.src = tk.StringVar(); self.out = tk.StringVar()
        self.flt = tk.StringVar(); self.st = tk.StringVar(value='就绪')
        self.localize = tk.BooleanVar(value=False)
        self.inject_prefix = tk.StringVar(value='/gaius/')
        self.inject_files = []
        self.pack = None; self.fmt = None; self.tmap = {}
        self.lq = queue.Queue(); self.worker = Worker(self.lq)
        self.flt.trace_add('write', lambda *a: self._tree())
        self._ui()
        self._log(f'启动 {BUILD_TAG}')
        self._log('自动识别: Gaius HTML / GAIUSVP1 / Eagler26 单文件')
        root.after(50, self._drain); root.after(500, lambda: self.sf.refresh())

    def _fonts(self):
        fams = set(tkfont.families(self.root))
        f = next((n for n in ['Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑', 'PingFang SC'] if n in fams), 'TkDefaultFont')
        for n in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont', 'TkCaptionFont', 'TkTooltipFont'):
            try: tkfont.nametofont(n).configure(family=f, size=9)
            except Exception: pass
        mono = 'Consolas' if 'Consolas' in fams else 'Courier New'
        try: tkfont.nametofont('TkFixedFont').configure(family=mono, size=9)
        except Exception: pass
        self.mono = mono

    def _ui(self):
        self.sf = ScrollFrame(self.root); self.sf.pack(fill='both', expand=True)
        c = self.sf.inner

        t = ttk.LabelFrame(c, text='1. 文件', padding=8); t.pack(fill='x', padx=10, pady=(10, 4))
        ttk.Label(t, text='输入:').grid(row=0, column=0, sticky='w')
        ttk.Entry(t, textvariable=self.src).grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(t, text='打开...', command=self.open).grid(row=0, column=2, padx=2)
        ttk.Label(t, text='输出:').grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Entry(t, textvariable=self.out).grid(row=1, column=1, sticky='ew', padx=4, pady=(4, 0))
        ttk.Button(t, text='另存为...', command=self.pick).grid(row=1, column=2, padx=2, pady=(4, 0))
        t.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(c, text='2. 选项', padding=8); opt.pack(fill='x', padx=10, pady=4)
        ttk.Checkbutton(opt, text='启动器汉化（仅 Gaius HTML 生效）', variable=self.localize).pack(anchor='w')

        info = ttk.LabelFrame(c, text='3. 包信息', padding=8); info.pack(fill='x', padx=10, pady=4)
        self.li = ttk.Label(info, text='（未加载）', justify='left'); self.li.pack(anchor='w')

        mid = ttk.Frame(c); mid.pack(fill='both', expand=True, padx=10, pady=4)
        l = ttk.LabelFrame(mid, text='4. 资源树', padding=6); l.pack(side='left', fill='both', expand=True)
        s2 = ttk.Frame(l); s2.pack(fill='x', pady=(0, 4))
        ttk.Label(s2, text='过滤:').pack(side='left')
        ttk.Entry(s2, textvariable=self.flt).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(s2, text='清空', command=lambda: self.flt.set('')).pack(side='left')
        tc = ttk.Frame(l); tc.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tc, columns=('s',), show='tree headings', selectmode='extended', height=16)
        self.tree.heading('#0', text='路径'); self.tree.heading('s', text='大小')
        self.tree.column('#0', width=460, minwidth=220); self.tree.column('s', width=100, anchor='e')
        vs = ttk.Scrollbar(tc, orient='vertical', command=self.tree.yview)
        hs = ttk.Scrollbar(tc, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky='nsew'); vs.grid(row=0, column=1, sticky='ns'); hs.grid(row=1, column=0, sticky='ew')
        tc.rowconfigure(0, weight=1); tc.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.st.set(f"选中 {len(self._sel())} 个"))
        self.tree.bind('<Double-1>', lambda e: self.preview())

        r = ttk.LabelFrame(mid, text='5. 预览', padding=6); r.pack(side='right', fill='both', expand=True, padx=(6, 0))
        self.pt = scrolledtext.ScrolledText(r, wrap='none', height=16, bg='#1e1e1e', fg='#d4d4d4', font=(self.mono, 10))
        self.pt.pack(fill='both', expand=True)

        o = ttk.LabelFrame(c, text='6. 操作', padding=6); o.pack(fill='x', padx=10, pady=4)
        r1 = ttk.Frame(o); r1.pack(fill='x', pady=2)
        for txt, cmd in [('添加...', self.add), ('替换...', self.rep), ('重命名...', self.ren), ('删除', self.del_)]:
            ttk.Button(r1, text=txt, command=cmd).pack(side='left', padx=2)
        r2 = ttk.Frame(o); r2.pack(fill='x', pady=2)
        for txt, cmd in [('导出选中...', self.exp1), ('导出全部...', self.expall), ('预览', self.preview), ('导出裸包...', self.expraw)]:
            ttk.Button(r2, text=txt, command=cmd).pack(side='left', padx=2)

        inj = ttk.LabelFrame(c, text='7. IndexedDB 注入（仅 Gaius HTML 生效）', padding=8)
        inj.pack(fill='x', padx=10, pady=4)
        ttk.Label(inj, text='目标前缀:').grid(row=0, column=0, sticky='w')
        ttk.Entry(inj, textvariable=self.inject_prefix).grid(row=0, column=1, sticky='ew', padx=4)
        inj.columnconfigure(1, weight=1)
        fl = ttk.Frame(inj); fl.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        ttk.Button(fl, text='添加文件...', command=self.inj_add).pack(side='left', padx=2)
        ttk.Button(fl, text='移除选中', command=self.inj_rm).pack(side='left', padx=2)
        ttk.Button(fl, text='清空', command=self.inj_clr).pack(side='left', padx=2)
        self.injl = tk.Listbox(inj, height=4, font=(self.mono, 9))
        self.injl.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 0))

        r3 = ttk.Frame(o); r3.pack(fill='x', pady=(6, 2))
        ttk.Button(r3, text='保存并重打包 (Ctrl+S)', command=self.save).pack(side='left', padx=2)

        b = ttk.LabelFrame(c, text='8. 日志', padding=6); b.pack(fill='x', padx=10, pady=(4, 10))
        self.lt = scrolledtext.ScrolledText(b, wrap='none', height=10, bg='#111', fg='#d4d4d4', font=(self.mono, 10))
        self.lt.pack(fill='both', expand=True)

        ttk.Label(self.root, textvariable=self.st, relief='sunken', anchor='w').pack(fill='x', side='bottom')
        self.root.bind('<Control-s>', lambda e: self.save())
        self.root.bind('<Control-o>', lambda e: self.open())
        self.root.bind('<Delete>', lambda e: self.del_())

    def _log(self, m): self.lq.put(('i', m))

    def _drain(self):
        try:
            n = 0
            while n < 300:
                _, m = self.lq.get_nowait(); self.lt.insert('end', m + '\n'); n += 1
            self.lt.see('end')
        except queue.Empty: pass
        self.root.after(80, self._drain)

    def open(self):
        p = filedialog.askopenfilename(filetypes=[('HTML / Pack', '*.html *.htm *.gz *.pack *.bin'), ('所有', '*.*')])
        if not p: return
        self.src.set(p)
        if not self.out.get():
            b, e = os.path.splitext(p); self.out.set(b + '_modified' + (e or '.html'))
        self.st.set('加载中...'); self.root.config(cursor='watch')
        self.worker.submit(self._load_bg, p)

    def _load_bg(self, p, cancel):
        try:
            self.pack, self.fmt = load_any(p, self._log, cancel)
            self.root.after(0, self._done)
        except Exception as e:
            self._log(f'[✗] {e}'); self._log(traceback.format_exc())
            self.root.after(0, lambda: messagebox.showerror('失败', str(e)))
            self.root.after(0, lambda: self.root.config(cursor=''))

    def _done(self):
        self._tree(); self._info(); self.st.set('就绪'); self.root.config(cursor='')
        self.root.after(200, lambda: self.sf.refresh())

    def _info(self):
        if not self.pack: self.li.config(text='（未加载）'); return
        if self.fmt == 'eagler':
            s = self.pack.stats()
            self.li.config(text=f"格式: Eagler26    一级: {s['files']}  assets: {s['assets_files']}  "
                                f"audio: {s['audio_files']}  "
                                f"{'已修改' if self.pack.is_modified() else '未修改'}")
        else:
            s = self.pack.stats()
            self.li.config(text=f"格式: Gaius    条目: {s['files']}    数据区: "
                                f"{s['payload_bytes']/1024/1024:.2f} MB")

    def _tree(self):
        self.tree.delete(*self.tree.get_children()); self.tmap.clear()
        if not self.pack: return
        kw = self.flt.get().lower().strip()
        tree = {}
        def add(v):
            if kw and kw not in v.lower(): return
            n = tree
            for d in v.split('/')[:-1]: n = n.setdefault(d, {})
            n.setdefault('__f__', []).append(v)
        if self.fmt == 'eagler':
            for p in self.pack.res: add(p)
            if self.pack.assets:
                for p in self.pack.assets.all_paths(): add(E_A + p)
            if self.pack.audio:
                for l in self.pack.audio.ranges:
                    for p in self.pack.audio.all_paths(l): add(E_U + l + '/' + p)
        else:
            for p in self.pack.paths(): add(p)
        self._ins('', tree); self.root.after(200, lambda: self.sf.refresh())

    def _sz(self, v):
        try:
            if self.fmt == 'eagler':
                if v.startswith(E_A):
                    p = v[len(E_A):]; a = self.pack.assets
                    if p in a.dirty: return len(a.dirty[p])
                    if p in a.new: return len(a.new[p])
                    r = a.ranges.get(p); return r[1] if r else 0
                if v.startswith(E_U):
                    l, _, p = v[len(E_U):].partition('/'); a = self.pack.audio
                    if p in a.dirty[l]: return len(a.dirty[l][p])
                    if p in a.new[l]: return len(a.new[l][p])
                    r = a.ranges[l].get(p); return r[1] if r else 0
                return len(self.pack.res.get(v, b''))
            return len(self.pack.read(v))
        except Exception: return 0

    def _ins(self, pid, n):
        for name in sorted(k for k in n if k != '__f__'):
            it = self.tree.insert(pid, 'end', text=name, values=('',), open=False)
            self._ins(it, n[name])
        for v in sorted(n.get('__f__', [])):
            it = self.tree.insert(pid, 'end', text=v.split('/')[-1], values=(f'{self._sz(v)}',))
            self.tmap[it] = v

    def _sel(self): return [self.tmap[i] for i in self.tree.selection() if i in self.tmap]

    def preview(self):
        p = self._sel()
        if not p: return
        v = p[0]; self.st.set(f'读 {v}')
        self.root.config(cursor='watch')
        self.worker.submit(self._pv_bg, v)

    def _pv_bg(self, v, cancel):
        try: d = self.pack.read(v)
        except Exception as e:
            self._log(f'[✗] {e}'); self.root.after(0, lambda: self.root.config(cursor='')); return
        self.root.after(0, lambda: self._pv_show(v, d))

    def _pv_show(self, v, d):
        self.pt.delete('1.0', 'end')
        self.pt.insert('end', f'// {v}\n// {len(d)} 字节\n' + '=' * 60 + '\n\n')
        try:
            t = d.decode('utf-8')
            if v.endswith('.json'):
                try: t = json.dumps(json.loads(t), ensure_ascii=False, indent=2)
                except Exception: pass
            if len(t) > 800000: t = t[:800000] + f'\n\n... 截断，原长 {len(t)}'
            self.pt.insert('end', t)
        except UnicodeDecodeError:
            hx = binascii.hexlify(d[:4096]).decode()
            self.pt.insert('end', ' '.join(hx[i:i + 2] for i in range(0, len(hx), 2)))
        self.st.set('就绪'); self.root.config(cursor='')

    def add(self):
        if not self.pack: return
        ps = filedialog.askopenfilenames()
        if not ps: return
        for p in ps:
            dlg = TextIn(self.root, '包内路径', '输入包内路径:', os.path.basename(p))
            if dlg.result is None: continue
            t = dlg.result.strip().lstrip('/')
            if not t: continue
            with open(p, 'rb') as f: c = f.read()
            a = self.pack.add(t, c); self._log(f'[{a}] {t} ({len(c)} B)')
        self._tree(); self._info()

    def rep(self):
        p = self._sel()
        if not p or len(p) > 1: return
        f = filedialog.askopenfilename()
        if not f: return
        with open(f, 'rb') as fh: c = fh.read()
        self.pack.write(p[0], c); self._tree(); self._info()
        self._log(f'[replaced] {p[0]} ({len(c)} B)')

    def ren(self):
        p = self._sel()
        if not p or len(p) > 1: return
        old = p[0]
        dlg = TextIn(self.root, '重命名', '新路径:', old)
        if dlg.result is None: return
        n = dlg.result.strip().lstrip('/')
        if not n or n == old: return
        try:
            if self.pack.rename(old, n):
                self._tree(); self._info(); self._log(f'[✓] {old} -> {n}')
        except Exception as e: messagebox.showerror('失败', str(e))

    def del_(self):
        p = self._sel()
        if not p: return
        if not messagebox.askyesno('确认', f'删除 {len(p)} 个？'): return
        for x in p: self.pack.delete(x)
        self._tree(); self._info()

    def exp1(self):
        p = self._sel()
        if not p: return
        if len(p) == 1:
            f = filedialog.asksaveasfilename(initialfile=os.path.basename(p[0]))
            if not f: return
            self.worker.submit(self._e1, p[0], f); return
        d = filedialog.askdirectory()
        if not d: return
        self.worker.submit(self._em, p, d)

    def _e1(self, v, o, cancel):
        try:
            with open(o, 'wb') as f: f.write(self.pack.read(v))
            self._log(f'[✓] {v} -> {o}')
        except Exception as e: self._log(f'[✗] {e}')

    def _em(self, ps, d, cancel):
        n = 0
        for x in ps:
            if cancel.is_set(): break
            r = x
            if self.fmt == 'eagler':
                if r.startswith(E_A): r = 'assets/' + r[len(E_A):]
                elif r.startswith(E_U): r = 'audio/' + r[len(E_U):]
            full = os.path.join(d, r.lstrip('/').replace('..', '_'))
            os.makedirs(os.path.dirname(full) or d, exist_ok=True)
            try:
                with open(full, 'wb') as f: f.write(self.pack.read(x))
                n += 1
            except Exception as e: self._log(f'[✗] {x}: {e}')
        self._log(f'[✓] 导出 {n}/{len(ps)}')

    def expall(self):
        if not self.pack: return
        d = filedialog.askdirectory()
        if not d: return
        if self.fmt == 'eagler':
            ps = list(self.pack.res)
            if self.pack.assets: ps += [E_A + p for p in self.pack.assets.all_paths()]
            if self.pack.audio:
                for l in self.pack.audio.ranges:
                    ps += [E_U + l + '/' + p for p in self.pack.audio.all_paths(l)]
        else:
            ps = list(self.pack.paths())
        self.worker.submit(self._em, ps, d)

    def expraw(self):
        if not self.pack: return
        if self.fmt != 'gaius':
            messagebox.showinfo('不支持', 'Eagler26 无裸包导出'); return
        out = filedialog.asksaveasfilename(defaultextension='.gz', filetypes=[('gzip', '*.gz'), ('所有', '*.*')])
        if not out: return
        with open(out, 'wb') as f: f.write(self.pack.to_gzip())
        self._log(f'[✓] 裸包 {out} ({os.path.getsize(out)/1024/1024:.2f} MB)')

    def inj_add(self):
        ps = filedialog.askopenfilenames()
        for p in ps: self.inject_files.append(p)
        self._inj_refresh()

    def inj_rm(self):
        for i in reversed(list(self.injl.curselection())):
            del self.inject_files[i]
        self._inj_refresh()

    def inj_clr(self):
        self.inject_files.clear(); self._inj_refresh()

    def _inj_refresh(self):
        self.injl.delete(0, 'end')
        for p in self.inject_files:
            self.injl.insert('end', f'{os.path.basename(p)}  <-  {p}')

    def pick(self):
        p = filedialog.asksaveasfilename(defaultextension='.html',
                                          filetypes=[('HTML', '*.html'), ('gzip', '*.gz'), ('所有', '*.*')])
        if p: self.out.set(p)

    def save(self):
        if not self.pack: return
        o = self.out.get()
        if not o:
            self.pick(); o = self.out.get()
            if not o: return
        self.st.set('打包中...'); self.root.config(cursor='watch')
        self.worker.submit(self._save_bg, o)

    def _save_bg(self, o, cancel):
        try:
            if self.fmt == 'eagler':
                self.pack.to_html(o, self._log, cancel)
            else:
                if self.src.get().lower().endswith(('.html', '.htm')) and o.lower().endswith(('.html', '.htm')):
                    inject = []
                    for p in self.inject_files:
                        with open(p, 'rb') as f: inject.append((os.path.basename(p), f.read()))
                    prefix = self.inject_prefix.get().strip() or '/gaius/'
                    if not prefix.endswith('/'): prefix += '/'
                    gaius_build_html(self.src.get(), self.pack, o,
                                     localize=self.localize.get(),
                                     inject_files=inject or None,
                                     target_prefix=prefix)
                    self._log(f'[✓] 写出 {o}')
                else:
                    with open(o, 'wb') as f: f.write(self.pack.to_gzip())
                    self._log(f'[✓] 写出 {o}')
            self.root.after(0, lambda: self._sd(o))
        except Exception as e:
            self._log(f'[✗] {e}'); self._log(traceback.format_exc())
            self.root.after(0, lambda: messagebox.showerror('失败', str(e)))
            self.root.after(0, lambda: self.root.config(cursor=''))

    def _sd(self, o):
        self._info(); self.st.set('完成'); self.root.config(cursor='')
        sz = os.path.getsize(o)
        self._log(f'[✓] {o} ({sz/1024/1024:.2f} MB)')
        messagebox.showinfo('完成', f'{o}\n\n{sz/1024/1024:.2f} MB')


def main():
    r = tk.Tk()
    try:
        s = ttk.Style()
        s.theme_use('vista' if 'vista' in s.theme_names() else 'clam')
    except Exception: pass
    GUI(r); r.mainloop()


if __name__ == '__main__':
    main()