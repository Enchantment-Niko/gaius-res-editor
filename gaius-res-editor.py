#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import sys
import gzip
import json
import base64
import struct
import binascii
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, scrolledtext

MAGIC = b'GAIUSVP1'
HEADER_SIZE = 12
DEFAULT_CHUNK = 8 * 1024 * 1024


class GaiusPack:
    def __init__(self, index=None, data=None):
        self.index = index or {}
        self.data = bytearray(data or b'')

    @classmethod
    def from_raw(cls, raw: bytes):
        if raw[:8] != MAGIC:
            raise ValueError("不是 GAIUSVP1 包")
        index_len = struct.unpack_from('<I', raw, 8)[0]
        start = HEADER_SIZE
        end = start + index_len
        if end > len(raw):
            raise ValueError("索引越界")
        index = json.loads(raw[start:end].decode('utf-8'))
        data = raw[end:]
        clean = {}
        for path, rng in index.items():
            if not isinstance(rng, list) or len(rng) != 2:
                raise ValueError(f"索引项非法: {path}")
            off, length = int(rng[0]), int(rng[1])
            if off < 0 or length < 0 or off + length > len(data):
                raise ValueError(f"索引越界: {path}")
            clean[path] = [off, length]
        return cls(clean, bytearray(data))

    def to_raw(self, compact=True):
        if compact:
            self._compact()
        idx = json.dumps(self.index, separators=(',', ':')).encode('utf-8')
        return MAGIC + struct.pack('<I', len(idx)) + idx + bytes(self.data)

    def to_gzip(self, level=9, compact=True):
        return gzip.compress(self.to_raw(compact=compact), compresslevel=level)

    def _compact(self):
        new_data = bytearray()
        new_index = {}
        for path in sorted(self.index.keys()):
            off, length = self.index[path]
            new_off = len(new_data)
            new_data += self.data[off:off + length]
            new_index[path] = [new_off, length]
        self.data = new_data
        self.index = new_index

    def paths(self):
        return list(self.index.keys())

    def has(self, path):
        return path in self.index

    def read(self, path):
        off, length = self.index[path]
        return bytes(self.data[off:off + length])

    def add(self, path, content):
        if path in self.index:
            return self.replace(path, content)
        off = len(self.data)
        self.data += content
        self.index[path] = [off, len(content)]
        return 'added'

    def replace(self, path, content):
        if path not in self.index:
            return self.add(path, content)
        off, old_len = self.index[path]
        if len(content) <= old_len:
            self.data[off:off + len(content)] = content
            if len(content) < old_len:
                self.data[off + len(content):off + old_len] = \
                    b'\x00' * (old_len - len(content))
            self.index[path] = [off, len(content)]
            return 'replaced-inplace'
        new_off = len(self.data)
        self.data += content
        self.index[path] = [new_off, len(content)]
        return 'replaced-appended'

    def delete(self, path):
        if path not in self.index:
            return False
        del self.index[path]
        return True

    def rename(self, old, new):
        if old not in self.index or old == new:
            return False
        if new in self.index:
            raise ValueError(f"目标路径已存在: {new}")
        self.index[new] = self.index.pop(old)
        return True

    def stats(self):
        return {'files': len(self.index), 'payload_bytes': len(self.data)}


_VANILLA_KEYS = (
    '"vanilla":[',
    "'vanilla':[",
    'vanilla:[',
    '"vanilla": [',
    "'vanilla': [",
    'vanilla: [',
    '"vanilla" :[',
    '"vanilla" : [',
    'vanilla\t:[',
    'vanilla\t: [',
)


def _find_matching_bracket(text, open_pos):
    depth = 0
    in_str = False
    escape = False
    i = open_pos
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValueError("找不到匹配的 ']'")


def extract_vanilla_chunks(html, debug=False):
    for key in _VANILLA_KEYS:
        pos = html.find(key)
        if pos < 0:
            continue
        open_pos = html.index('[', pos)
        close_pos = _find_matching_bracket(html, open_pos)
        body = html[open_pos + 1:close_pos]
        chunks = re.findall(r'"([^"]*)"', body)
        if debug:
            print(f"[DBG] 匹配 key={key!r} 位置={pos} chunks={len(chunks)}")
        return chunks, open_pos, close_pos
    if debug:
        idx = html.find('vanilla')
        if idx >= 0:
            print(f"[DBG] 找到 'vanilla' 在 {idx}，附近:")
            print(repr(html[max(0, idx - 80):idx + 200]))
        else:
            print("[DBG] HTML 里完全没有 'vanilla'")
    return None


def replace_vanilla_chunks(html, chunks):
    found = extract_vanilla_chunks(html)
    if not found:
        raise ValueError("HTML 里找不到 vanilla 数组")
    _, open_pos, close_pos = found
    new_array = '[\n' + ',\n'.join('  "' + c + '"' for c in chunks) + '\n]'
    return html[:open_pos] + new_array + html[close_pos + 1:]


def load_from_html(html_path, debug=True):
    with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()
    found = extract_vanilla_chunks(html, debug=debug)
    if not found:
        raise ValueError("这不是 Gaius 的 HTML（找不到 vanilla 数组）")
    chunks, _, _ = found
    if debug:
        print(f"[DBG] 开始解码 {len(chunks)} 个 base64 分片...")
    raw = b''.join(base64.b64decode(c) for c in chunks)
    if debug:
        print(f"[DBG] base64 解码后 {len(raw)} 字节，gzip 头: {raw[:4].hex()}")
    decompressed = gzip.decompress(raw)
    if debug:
        print(f"[DBG] gzip 解压后 {len(decompressed)} 字节，"
              f"magic: {decompressed[:8]!r}")
    return GaiusPack.from_raw(decompressed)


def build_html(html_path, pack, out_path, localize_boot=False,
               inject_files=None, target_prefix='/gaius/', debug=True):
    with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()
    gz = pack.to_gzip()
    if debug:
        print(f"[DBG] 重新压缩后 {len(gz)} 字节")
    chunks = [
        base64.b64encode(gz[i:i + DEFAULT_CHUNK]).decode('ascii')
        for i in range(0, len(gz), DEFAULT_CHUNK)
    ]
    if debug:
        print(f"[DBG] base64 分片 {len(chunks)} 块")
    html = replace_vanilla_chunks(html, chunks)
    if localize_boot:
        html = localize_launcher_text(html, debug=debug)
    if inject_files:
        html = inject_indexeddb_into_html(html, inject_files,
                                          target_prefix=target_prefix,
                                          debug=debug)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


def load_from_file(path, debug=True):
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\x1f\x8b':
        if debug:
            print(f"[DBG] gzip 输入，解压中...")
        raw = gzip.decompress(raw)
    if debug:
        print(f"[DBG] magic: {raw[:8]!r}")
    return GaiusPack.from_raw(raw)


_BOOT_REPLACEMENTS = [
    ('"ONLINE SESSION"', '"在线会话"', False),
    ('"PORTABLE HTML"', '"便携 HTML"', False),
    ('"BROWSER CLIENT"', '"浏览器客户端"', False),
    (r'VERSION (\d+\.\d+\.\d+)', r'版本 \1', True),
    ('Gaius Client</strong> | independent browser software',
     'Gaius 客户端</strong> | 独立浏览器软件', False),
    ('HTML runtime | local storage enabled',
     'HTML 运行时 | 本地存储已启用', False),
    ('Retry startup', '重试启动', False),
    ('Show diagnostics', '显示诊断', False),
    ('Hide diagnostics', '隐藏诊断', False),
    ('BROWSER CLIENT', '浏览器客户端', False),
    ('Starting Gaius Client 26.3...', '正在启动 Gaius 客户端 26.3...', False),
    ('"Loading persistent browser storage..."', '"正在加载浏览器持久化存储..."', False),
    ('"Browser storage is ready ("', '"浏览器存储已就绪 ("', False),
    ('"); loading classes.js..."', '"); 正在加载 classes.js..."', False),
    ('"classes.js loaded; calling net.minecraft.client.main.Main.main(args)...\\n"',
     '"classes.js 已加载; 正在调用 net.minecraft.client.main.Main.main(args)...\\n"',
     False),
    ('"Browser runtime error:"', '"浏览器运行时错误:"', False),
    ('"Unhandled browser promise rejection:"', '"未处理的浏览器 Promise 拒绝:"', False),
    ('"Gaius Client failed to start:"', '"Gaius 客户端启动失败:"', False),
    ('"0% Initializing..."', '"0% 初始化中..."', False),
]


def localize_launcher_text(html, debug=True):
    count = 0
    missed = []
    for old, new, is_regex in _BOOT_REPLACEMENTS:
        if is_regex:
            new_html, n = re.subn(old, new, html)
        else:
            n = html.count(old)
            new_html = html.replace(old, new)
        if n > 0:
            html = new_html
            count += n
            if debug:
                print(f"[ZH] x{n} {old[:60]!r}")
        else:
            missed.append(old)
    if debug:
        if missed:
            print(f"[ZH] 未匹配 {len(missed)} 条:")
            for m in missed[:10]:
                print(f"     {m[:70]!r}")
        print(f"[ZH] 共替换 {count} 处")
    return html


INJECT_TEMPLATE = '''
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
    try {
      await (window.__gaiusFsReady || Promise.resolve());
    } catch (_) {}

    for (const entry of INJECT_FILES) {
      const targetPath = TARGET_PREFIX + entry.path;
      const bytes = b64ToBytes(entry.data);

      if (typeof window.__gaiusFsPutBytes === "function") {
        if (window.__gaiusFsPutBytes(targetPath, bytes)) {
          log("wrote " + targetPath + " via __gaiusFsPutBytes");
          continue;
        }
      }
      if (typeof window.__gaiusFsPut === "function") {
        if (window.__gaiusFsPut(targetPath, entry.data)) {
          log("wrote " + targetPath + " via __gaiusFsPut");
          continue;
        }
      }
      try {
        const dbName = window.__gaiusStorageDatabaseName || "gaius-fs-v2-26.3";
        const storeName = "files";
        await new Promise((resolve) => {
          const openReq = indexedDB.open(dbName);
          openReq.onsuccess = () => {
            const db = openReq.result;
            const tx = db.transaction(storeName, "readwrite");
            tx.objectStore(storeName).put({
              path: targetPath,
              value: bytes,
              updatedAt: Date.now()
            });
            tx.oncomplete = () => {
              log("wrote " + targetPath + " via IndexedDB");
              db.close();
              resolve();
            };
            tx.onerror = () => {
              log("IndexedDB transaction failed: " + tx.error);
              resolve();
            };
          };
          openReq.onerror = () => {
            log("IndexedDB open failed: " + openReq.error);
            resolve();
          };
        });
      } catch (e) {
        log("exception: " + e);
      }
    }
  }

  if (document.readyState === "complete") {
    setTimeout(writeAll, 2000);
  } else {
    window.addEventListener("load", () => setTimeout(writeAll, 2000));
  }
})();
</script>
'''


def inject_indexeddb_into_html(html, files, target_prefix='/gaius/', debug=True):
    payload = []
    for path, data in files:
        b64 = base64.b64encode(data).decode('ascii')
        payload.append({"path": path.lstrip('/'), "data": b64})
    snippet = (INJECT_TEMPLATE
               .replace('__INJECT_FILES__', json.dumps(payload))
               .replace('__TARGET_PREFIX__', target_prefix))
    if "gaiusIndexedDbInjector" in html:
        raise ValueError("这个 HTML 已经注入过 IndexedDB 注入器了")
    idx = html.rfind("</body>")
    if idx < 0:
        html += snippet
    else:
        html = html[:idx] + snippet + html[idx:]
    if debug:
        for path, data in files:
            print(f"[INJ] {path}: {len(data)} 字节")
        print(f"[INJ] 目标前缀: {target_prefix}")
    return html


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient='vertical',
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

        self.inner = ttk.Frame(self.canvas)
        self.inner_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor='nw')

        self.inner.bind('<Configure>', self._on_inner_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        self._on_wheel = self._make_wheel_handler()
        self.bind_all_wheel_recursive(self.inner)

    def _make_wheel_handler(self):
        def _on_wheel(event):
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta * 3, 'units')
        return _on_wheel

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.inner_id, width=event.width)

    def bind_all_wheel_recursive(self, widget):
        widget.bind('<MouseWheel>', self._on_wheel, add='+')
        widget.bind('<Button-4>', self._on_wheel, add='+')
        widget.bind('<Button-5>', self._on_wheel, add='+')
        for child in widget.winfo_children():
            self.bind_all_wheel_recursive(child)

    def refresh_wheel_bindings(self):
        self.bind_all_wheel_recursive(self.inner)


class TextInputDialog(tk.Toplevel):
    def __init__(self, parent, title, label, default=''):
        super().__init__(parent)
        self.title(title)
        self.geometry('640x180')
        self.transient(parent)
        self.grab_set()
        self.result = None
        ttk.Label(self, text=label).pack(anchor='w', padx=15, pady=(15, 4))
        self.var = tk.StringVar(value=default)
        e = ttk.Entry(self, textvariable=self.var)
        e.pack(fill='x', padx=15, pady=4)
        e.focus_set()
        e.select_range(0, 'end')
        btns = ttk.Frame(self)
        btns.pack(fill='x', padx=15, pady=10)
        ttk.Button(btns, text='确定', command=self._ok).pack(side='right', padx=4)
        ttk.Button(btns, text='取消', command=self.destroy).pack(side='right', padx=4)
        self.bind('<Return>', lambda e: self._ok())
        self.bind('<Escape>', lambda e: self.destroy())
        self.wait_window()

    def _ok(self):
        self.result = self.var.get()
        self.destroy()


class GaiusGUI:
    def __init__(self, root):
        self.root = root
        root.title('Gaius 资源包编辑器 + IndexedDB 注入器')
        root.geometry('1200x780')
        root.minsize(900, 560)

        self._setup_fonts()

        self.source_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.status_var = tk.StringVar(value='就绪')
        self.localize_boot_var = tk.BooleanVar(value=False)

        self.inject_prefix = tk.StringVar(value='/gaius/')
        self.inject_files = []

        self.pack = None
        self.is_html = False
        self.dirty = False
        self._tree_map = {}
        self._dir_map = {}

        self.filter_var.trace_add('write', lambda *a: self._rebuild_tree())

        self._build_ui()
        self._log('Gaius 资源包编辑器已启动')
        self._log('支持: Gaius HTML (embedded.vanilla) / GAIUSVP1 裸包')
        self._log('提示: 勾选「启动器汉化」后保存时会替换启动文本')
        self._log('提示: 添加注入文件后点「保存并注入」即可一起写出')

    def _setup_fonts(self):
        families = set(tkfont.families(self.root))
        preferred = ['Microsoft YaHei UI', 'Microsoft YaHei',
                     '微软雅黑', 'PingFang SC', 'Noto Sans CJK SC']
        chosen = None
        for name in preferred:
            if name in families:
                chosen = name
                break
        if chosen is None:
            chosen = 'TkDefaultFont'
        for font_name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont',
                          'TkHeadingFont', 'TkCaptionFont', 'TkTooltipFont'):
            try:
                f = tkfont.nametofont(font_name)
                f.configure(family=chosen, size=9)
            except Exception:
                pass
        mono = 'Consolas'
        if mono not in families:
            mono = 'Courier New'
        try:
            f = tkfont.nametofont('TkFixedFont')
            f.configure(family=mono, size=9)
        except Exception:
            pass
        self._font_family = chosen
        self._mono_family = mono

    def _build_ui(self):
        self.scroll_frame = ScrollableFrame(self.root)
        self.scroll_frame.pack(fill='both', expand=True)
        container = self.scroll_frame.inner

        top = ttk.LabelFrame(container, text='1. 文件', padding=8)
        top.pack(fill='x', padx=10, pady=(10, 4))
        ttk.Label(top, text='输入:').grid(row=0, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.source_path).grid(
            row=0, column=1, sticky='ew', padx=4)
        ttk.Button(top, text='打开...', command=self.open_file).grid(
            row=0, column=2, padx=2)
        ttk.Label(top, text='输出:').grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Entry(top, textvariable=self.output_path).grid(
            row=1, column=1, sticky='ew', padx=4, pady=(4, 0))
        ttk.Button(top, text='另存为...', command=self.pick_output).grid(
            row=1, column=2, padx=2, pady=(4, 0))
        top.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(container, text='2. 启动器汉化', padding=8)
        opt.pack(fill='x', padx=10, pady=4)
        ttk.Checkbutton(
            opt,
            text='注入后汉化启动器（仅对 HTML 输出生效）',
            variable=self.localize_boot_var,
        ).pack(anchor='w')
        ttk.Label(
            opt,
            text='勾选后，保存 HTML 时会自动替换启动文本（不碰 boot art）。',
            foreground='#666',
            justify='left',
        ).pack(anchor='w', pady=(4, 0))

        info = ttk.LabelFrame(container, text='3. 包信息', padding=8)
        info.pack(fill='x', padx=10, pady=4)
        self.info_label = ttk.Label(info, text='（未加载）', justify='left')
        self.info_label.pack(anchor='w')

        mid = ttk.Frame(container)
        mid.pack(fill='x', padx=10, pady=4)

        left = ttk.LabelFrame(mid, text='4. 条目', padding=6)
        left.pack(side='left', fill='both', expand=True)
        sf = ttk.Frame(left)
        sf.pack(fill='x', pady=(0, 4))
        ttk.Label(sf, text='过滤:').pack(side='left')
        ttk.Entry(sf, textvariable=self.filter_var).pack(
            side='left', fill='x', expand=True, padx=4)
        ttk.Button(sf, text='清空', command=lambda: self.filter_var.set('')).pack(
            side='left')

        tc = ttk.Frame(left)
        tc.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tc, columns=('size',), show='tree headings',
                                 selectmode='extended', height=10)
        self.tree.heading('#0', text='路径')
        self.tree.heading('size', text='大小')
        self.tree.column('#0', width=460, minwidth=200)
        self.tree.column('size', width=100, anchor='e', minwidth=70)
        vsb = ttk.Scrollbar(tc, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tc, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tc.rowconfigure(0, weight=1)
        tc.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree.bind('<Double-1>', lambda e: self.preview())

        right = ttk.Frame(mid)
        right.pack(side='right', fill='both', expand=True, padx=(6, 0))

        pv = ttk.LabelFrame(right, text='5. 预览', padding=6)
        pv.pack(fill='both', expand=True)
        self.preview_text = scrolledtext.ScrolledText(
            pv, wrap='none', height=10,
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='#d4d4d4',
            font=(self._mono_family, 10))
        self.preview_text.pack(fill='both', expand=True)

        ops = ttk.LabelFrame(container, text='6. 操作', padding=6)
        ops.pack(fill='x', padx=10, pady=4)

        r1 = ttk.Frame(ops)
        r1.pack(fill='x', pady=2)
        ttk.Button(r1, text='添加文件...', command=self.add_file).pack(
            side='left', padx=2)
        ttk.Button(r1, text='替换选中...', command=self.replace_selected).pack(
            side='left', padx=2)
        ttk.Button(r1, text='重命名...', command=self.rename_selected).pack(
            side='left', padx=2)
        ttk.Button(r1, text='删除', command=self.delete_selected).pack(
            side='left', padx=2)

        r2 = ttk.Frame(ops)
        r2.pack(fill='x', pady=2)
        ttk.Button(r2, text='导出选中...', command=self.export_selected).pack(
            side='left', padx=2)
        ttk.Button(r2, text='导出全部...', command=self.export_all).pack(
            side='left', padx=2)
        ttk.Button(r2, text='预览', command=self.preview).pack(
            side='left', padx=2)
        ttk.Button(r2, text='导出裸包...', command=self.export_raw).pack(
            side='left', padx=2)

        inj = ttk.LabelFrame(container, text='7. IndexedDB 注入（可选）', padding=8)
        inj.pack(fill='x', padx=10, pady=4)
        ttk.Label(inj, text='目标前缀:').grid(row=0, column=0, sticky='w')
        ttk.Entry(inj, textvariable=self.inject_prefix).grid(
            row=0, column=1, sticky='ew', padx=4)
        ttk.Label(
            inj,
            text='注入文件会写入 <前缀> + <文件名>。默认 /gaius/。',
            foreground='#666',
            justify='left',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        inj.columnconfigure(1, weight=1)

        fl = ttk.Frame(inj)
        fl.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        ttk.Button(fl, text='添加文件...', command=self.inject_add_file).pack(
            side='left', padx=2)
        ttk.Button(fl, text='移除选中', command=self.inject_remove_file).pack(
            side='left', padx=2)
        ttk.Button(fl, text='清空', command=self.inject_clear_files).pack(
            side='left', padx=2)

        self.inject_list = tk.Listbox(inj, height=5,
                                      font=(self._font_family, 9))
        self.inject_list.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(4, 0))

        r3 = ttk.Frame(ops)
        r3.pack(fill='x', pady=(6, 2))
        ttk.Button(r3, text='保存并注入 (Ctrl+S)',
                   command=self.save).pack(side='left', padx=2)

        bot = ttk.LabelFrame(container, text='8. 日志', padding=6)
        bot.pack(fill='x', padx=10, pady=(4, 10))
        self.log_text = scrolledtext.ScrolledText(
            bot, wrap='none', height=8,
            bg='#111', fg='#d4d4d4', insertbackground='#d4d4d4',
            font=(self._mono_family, 10))
        self.log_text.pack(fill='both', expand=True)

        ttk.Label(self.root, textvariable=self.status_var,
                  relief='sunken', anchor='w').pack(fill='x', side='bottom')

        self.root.bind('<Control-s>', lambda e: self.save())
        self.root.bind('<Control-o>', lambda e: self.open_file())
        self.root.bind('<Delete>', lambda e: self.delete_selected())

        self.root.after(500, self.scroll_frame.refresh_wheel_bindings)

    def _log(self, msg):
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.root.update_idletasks()

    def _set_status(self, msg):
        self.status_var.set(msg)

    def open_file(self):
        p = filedialog.askopenfilename(
            title='打开 Gaius HTML 或 GAIUSVP1 裸包',
            filetypes=[
                ('Gaius HTML / 裸包', '*.html *.htm *.gz *.pack *.bin'),
                ('所有文件', '*.*'),
            ])
        if not p:
            return
        self.source_path.set(p)
        if not self.output_path.get():
            base, ext = os.path.splitext(p)
            self.output_path.set(base + '_modified' + (ext or '.html'))
        self._load()

    def _load(self):
        path = self.source_path.get()
        if not path or not os.path.exists(path):
            messagebox.showwarning('缺少文件', '请先选择输入文件')
            return
        self._set_status('加载中...')
        self.root.config(cursor='watch')
        self.root.update()
        try:
            self.is_html = path.lower().endswith(('.html', '.htm'))
            if self.is_html:
                self.pack = load_from_html(path, debug=True)
            else:
                self.pack = load_from_file(path, debug=True)
            self.dirty = False
            self._rebuild_tree()
            self._refresh_info()
            self._log(f'[✓] 已加载: {path}')
            self._log(f'    条目数: {len(self.pack.index)}')
            self._log(f'    数据区: {len(self.pack.data)} 字节 '
                      f'({len(self.pack.data)/1024/1024:.2f} MB)')
            self._set_status('就绪')
            self.root.after(100, self.scroll_frame.refresh_wheel_bindings)
        except Exception as e:
            self._log(f'[✗] 加载失败: {e}')
            self._log(traceback.format_exc())
            messagebox.showerror('加载失败', str(e))
            self._set_status('加载失败')
        finally:
            self.root.config(cursor='')

    def _refresh_info(self):
        if not self.pack:
            self.info_label.config(text='（未加载）')
            return
        s = self.pack.stats()
        kind = 'Gaius HTML' if self.is_html else 'GAIUSVP1 裸包'
        text = (f"格式: {kind}    条目数: {s['files']}    "
                f"数据区: {s['payload_bytes']/1024/1024:.2f} MB    "
                f"已修改: {'是' if self.dirty else '否'}")
        self.info_label.config(text=text)

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_map.clear()
        self._dir_map.clear()
        if not self.pack:
            return
        kw = self.filter_var.get().lower().strip()
        paths = sorted(self.pack.index.keys())
        dirs = {}
        for path in paths:
            if kw and kw not in path.lower():
                continue
            parts = path.split('/')
            node = dirs
            for d in parts[:-1]:
                node = node.setdefault(d, {})
            node.setdefault('__files__', []).append(path)
        self._insert_dir('', dirs)
        self.root.after(100, self.scroll_frame.refresh_wheel_bindings)

    def _insert_dir(self, parent_id, node):
        for name in sorted(k for k in node.keys() if k != '__files__'):
            sub = node[name]
            item = self.tree.insert(parent_id, 'end', text=name,
                                    values=('',), open=False)
            self._dir_map[item] = name
            self._insert_dir(item, sub)
        for path in sorted(node.get('__files__', [])):
            length = self.pack.index[path][1]
            item = self.tree.insert(parent_id, 'end',
                                    text=path.split('/')[-1],
                                    values=(f'{length}',))
            self._tree_map[item] = path

    def _selected_paths(self):
        return [self._tree_map[i] for i in self.tree.selection()
                if i in self._tree_map]

    def _on_select(self, event=None):
        paths = self._selected_paths()
        if len(paths) == 1:
            self._set_status(f'选中: {paths[0]}')
        elif len(paths) > 1:
            self._set_status(f'选中 {len(paths)} 个条目')
        else:
            self._set_status('就绪')

    def preview(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo('未选中', '请先选择条目')
            return
        path = paths[0]
        try:
            data = self.pack.read(path)
        except Exception as e:
            messagebox.showerror('读取失败', str(e))
            return
        self.preview_text.delete('1.0', 'end')
        header = (f'// {path}\n'
                  f'// {len(data)} 字节\n'
                  + '=' * 60 + '\n\n')
        self.preview_text.insert('end', header)
        try:
            text = data.decode('utf-8')
            if path.endswith('.json'):
                try:
                    text = json.dumps(json.loads(text),
                                      ensure_ascii=False, indent=2)
                except Exception:
                    pass
            if len(text) > 800000:
                text = text[:800000] + f'\n\n... 截断，原长 {len(text)}'
            self.preview_text.insert('end', text)
        except UnicodeDecodeError:
            hx = binascii.hexlify(data[:4096]).decode()
            formatted = ' '.join(hx[i:i+2] for i in range(0, len(hx), 2))
            self.preview_text.insert(
                'end', f'[二进制，前 4096 字节]\n\n{formatted}')

    def add_file(self):
        if not self.pack:
            messagebox.showwarning('未加载', '请先打开文件')
            return
        ps = filedialog.askopenfilenames(title='选择要添加的文件（可多选）')
        if not ps:
            return
        added = 0
        for p in ps:
            default = os.path.basename(p)
            dlg = TextInputDialog(self.root, '包内路径',
                                  '输入包内路径:', default)
            if dlg.result is None:
                continue
            target = dlg.result.strip().lstrip('/')
            if not target:
                continue
            with open(p, 'rb') as f:
                content = f.read()
            action = self.pack.add(target, content)
            self.dirty = True
            added += 1
            self._log(f'[{action}] {target} ({len(content)} 字节)')
        if added:
            self._rebuild_tree()
            self._refresh_info()
            self._log(f'[✓] 已添加 {added} 个文件')

    def replace_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo('未选中', '请先选择条目')
            return
        if len(paths) > 1:
            messagebox.showinfo('多选', '替换一次只能选一个条目')
            return
        target = paths[0]
        p = filedialog.askopenfilename(
            title=f'选择替换 {target} 的文件')
        if not p:
            return
        with open(p, 'rb') as f:
            content = f.read()
        action = self.pack.replace(target, content)
        self.dirty = True
        self._rebuild_tree()
        self._refresh_info()
        self._log(f'[{action}] {target} ({len(content)} 字节)')

    def rename_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo('未选中', '请先选择条目')
            return
        if len(paths) > 1:
            messagebox.showinfo('多选', '重命名一次只能选一个条目')
            return
        old = paths[0]
        dlg = TextInputDialog(self.root, '重命名',
                              '新路径（可用 / 分隔子目录）:', old)
        if dlg.result is None:
            return
        new = dlg.result.strip().lstrip('/')
        if not new or new == old:
            return
        try:
            if self.pack.rename(old, new):
                self.dirty = True
                self._rebuild_tree()
                self._refresh_info()
                self._log(f'[✓] 重命名: {old} -> {new}')
        except Exception as e:
            messagebox.showerror('重命名失败', str(e))

    def delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo('未选中', '请先选择条目')
            return
        if not messagebox.askyesno('确认', f'删除 {len(paths)} 个条目？'):
            return
        count = 0
        for p in paths:
            if self.pack.delete(p):
                count += 1
                self._log(f'[✓] 已删除: {p}')
        if count:
            self.dirty = True
            self._rebuild_tree()
            self._refresh_info()

    def export_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo('未选中', '请先选择条目')
            return
        if len(paths) == 1:
            p = paths[0]
            default = os.path.basename(p)
            out = filedialog.asksaveasfilename(
                title='导出文件', initialfile=default)
            if not out:
                return
            with open(out, 'wb') as f:
                f.write(self.pack.read(p))
            self._log(f'[✓] 导出: {p} -> {out}')
            return
        out_dir = filedialog.askdirectory(title='批量导出到目录')
        if not out_dir:
            return
        count = 0
        for p in paths:
            rel = p.lstrip('/').replace('..', '_')
            full = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(full) or out_dir, exist_ok=True)
            with open(full, 'wb') as f:
                f.write(self.pack.read(p))
            count += 1
        self._log(f'[✓] 导出 {count} 个文件 -> {out_dir}')

    def export_all(self):
        if not self.pack:
            messagebox.showwarning('未加载', '请先打开文件')
            return
        out_dir = filedialog.askdirectory(title='选择导出目录')
        if not out_dir:
            return
        count = 0
        for p in self.pack.paths():
            rel = p.lstrip('/').replace('..', '_')
            full = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(full) or out_dir, exist_ok=True)
            with open(full, 'wb') as f:
                f.write(self.pack.read(p))
            count += 1
        self._log(f'[✓] 已导出 {count} 个文件 -> {out_dir}')

    def pick_output(self):
        p = filedialog.asksaveasfilename(
            title='保存输出', defaultextension='.html',
            filetypes=[('HTML', '*.html'), ('裸包', '*.gz'), ('所有', '*.*')])
        if p:
            self.output_path.set(p)

    def save(self):
        if not self.pack:
            messagebox.showwarning('未加载', '请先打开文件')
            return
        out = self.output_path.get()
        if not out:
            self.pick_output()
            out = self.output_path.get()
            if not out:
                return
        self._set_status('保存并注入中...')
        self.root.config(cursor='watch')
        self.root.update()
        try:
            localize = self.localize_boot_var.get()
            inject_files = []
            for p in self.inject_files:
                with open(p, 'rb') as f:
                    inject_files.append((os.path.basename(p), f.read()))

            prefix = self.inject_prefix.get().strip() or '/gaius/'
            if not prefix.endswith('/'):
                prefix += '/'

            if self.is_html and out.lower().endswith(('.html', '.htm')):
                if localize:
                    self._log('[ZH] 已启用启动器汉化')
                if inject_files:
                    self._log(f'[INJ] 将注入 {len(inject_files)} 个文件，前缀 {prefix}')
                build_html(self.source_path.get(), self.pack, out,
                           localize_boot=localize,
                           inject_files=inject_files or None,
                           target_prefix=prefix,
                           debug=True)
            else:
                with open(out, 'wb') as f:
                    f.write(self.pack.to_gzip())
                if localize:
                    self._log('[!] 导出裸包时汉化选项无效')
                if inject_files:
                    self._log('[!] 导出裸包时注入选项无效（裸包不含 HTML）')
            self.dirty = False
            self._refresh_info()
            size = os.path.getsize(out)
            self._log(f'[✓] 已写出: {out} ({size/1024/1024:.2f} MB)')
            self._set_status('完成')
            messagebox.showinfo('完成',
                                f'已写出:\n{out}\n\n'
                                f'{size/1024/1024:.2f} MB')
        except Exception as e:
            self._log(f'[✗] 失败: {e}')
            self._log(traceback.format_exc())
            messagebox.showerror('失败', str(e))
            self._set_status('失败')
        finally:
            self.root.config(cursor='')

    def export_raw(self):
        if not self.pack:
            messagebox.showwarning('未加载', '请先打开文件')
            return
        out = filedialog.asksaveasfilename(
            title='导出裸 GAIUSVP1 包', defaultextension='.gz',
            filetypes=[('gzip', '*.gz'), ('所有', '*.*')])
        if not out:
            return
        with open(out, 'wb') as f:
            f.write(self.pack.to_gzip())
        self._log(f'[✓] 已导出裸包: {out} '
                  f'({os.path.getsize(out)/1024/1024:.2f} MB)')

    def inject_add_file(self):
        ps = filedialog.askopenfilenames(
            title='选择要注入到 IndexedDB 的文件（可多选）')
        for p in ps:
            self.inject_files.append(p)
        self._refresh_inject_list()

    def inject_remove_file(self):
        sel = list(self.inject_list.curselection())
        for i in reversed(sel):
            del self.inject_files[i]
        self._refresh_inject_list()

    def inject_clear_files(self):
        self.inject_files.clear()
        self._refresh_inject_list()

    def _refresh_inject_list(self):
        self.inject_list.delete(0, 'end')
        for p in self.inject_files:
            self.inject_list.insert('end', f'{os.path.basename(p)}  <-  {p}')


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        elif 'clam' in style.theme_names():
            style.theme_use('clam')
    except Exception:
        pass
    GaiusGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()