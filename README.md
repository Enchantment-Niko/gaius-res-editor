# eagler26-editor

A tool to browse, modify, and repack resources inside Gaius and GoldHQ single-file HTML Minecraft builds.

浏览、修改、重打包 [Gaius](https://github.com/TypeThe0ry/Gaius) 与 GoldHQ 单文件 HTML 中的嵌入式资源。

## 简介

`eagler26-editor` 是一个用于编辑**单文件 HTML 客户端**中嵌入式资源的工具。它**自动识别**以下两种格式：

- **Gaius WebGL2 Minecraft** —— 解析 `embedded.vanilla` 数组 + `GAIUSVP1` 容器
- **GoldHQ** —— 解析 `application/x-eagler26-chunk` 分片 + 自定义转义 + Fletcher 校验

可以像操作普通资源包一样**浏览、修改、注入**资源，并可选对启动器文本进行汉化。

- 纯 Python 标准库实现，无第三方依赖
- 自动识别格式，打开即用
- GUI + 多线程，加载 / 保存不卡
- 支持单文件级 添加 / 替换 / 重命名 / 删除
- 支持导出单个 / 全部 / 裸包
- **Eagler26 额外支持二级资源**：`assets.idx` + `assets.bin`、`audio/*.idx` + `*.bin`
- 可选启动器文本汉化（Gaius HTML）

## 前置准备

**本工具不附带客户端 HTML。** 你需要自行获取对应的单文件 HTML：

- **GoldHQ 客户端**: https://client.goldhq.net/
- **Gaius Releases 构建**: https://github.com/TypeThe0ry/Gaius/releases

下载后得到一个 `.html` 文件，**直接用它作为输入**。

**如果客户端更新、格式变化，工具可能失效**。遇到问题请把新版 HTML 的 `eagler26-offline-meta`（Eagler26）或 `embedded.vanilla`（Gaius）内容反馈。

---

## 功能介绍

### 自动识别

打开文件时自动嗅探格式：

| 格式 | 识别特征 |
|---|---|
| Gaius | `embedded.vanilla` 数组 / `GAIUSVP1` magic |
| GoldHQ | `application/x-eagler26-chunk` / `eagler26-offline-meta` |

无需手动选择，打开即用。

---

### Gaius 格式

#### 解析

- 从 Gaius 单文件 HTML 中提取 `embedded.vanilla` 的 base64 分片
- 拼接、gunzip、解析 `GAIUSVP1` 容器
- 读取 JSON 索引与数据区
- 支持直接打开裸包（`.gz` / `.pack` / `GAIUSVP1`）

#### 写回

- 写回 HTML：重建 `GAIUSVP1` → gzip → base64 分片 → 替换 `vanilla` 数组
- 写回裸包：直接输出 gzip 后的 `GAIUSVP1`

#### 启动器汉化

- 覆盖初始状态、进度文案、状态文案、错误提示、外壳 UI、按钮文案
- 仅对 HTML 输出生效；导出裸包时此选项无效
- GUI 里用复选框开关

---

### GoldHQ 格式

#### 解析

- 扫描 `<script type="application/x-eagler26-chunk" data-r="..." data-len="..." data-sum="...">`
- 按 `windows-1252` 解码，遇到 `ESC` 转义（默认 `0x3D` = `=`）走 `unsafe` 表
- 每片用 **Fletcher 变种校验**（逐字节，自然溢出 2^32，最后 `(s1 & 0xffff) ^ (s2 << 16)`）
- 按 HTML 中的出现顺序拼接多片
- 解析 `eagler26-offline-meta` 与 `eagler26-build` 两个清单

#### 一级资源

所有顶层 chunk：

- `boot-screen.js`
- `browser-*.js`
- `client.js`
- `wasm/*.wasm`、`*.wasm-runtime.js`
- `assets/assets.idx`、`assets/assets.bin`
- `audio/*.idx`、`*.bin`

#### 二级资源（虚拟目录）

| 虚拟前缀 | 内容 |
|---|---|
| `__ASSETS__/...` | `assets.idx` + `assets.bin` 展开后的 21500+ 个 MC 资源（纹理、模型、语言、字体、splashes…） |
| `__AUDIO__/sfx/...` | `audio/sfx-*.idx` + `*.bin` 展开的音效 |
| `__AUDIO__/music/...` | 背景音乐 |
| `__AUDIO__/records/...` | 唱片 |
| `__AUDIO__/sfx-opt/...` | 优化版音效 |

**这些虚拟目录只是 GUI 里的组织方式，写回时仍按原格式重打包，不改变最终结构。**

#### 写回

- 重建 `assets.idx` / `assets.bin`（如果二级被改动）
- 重建 `audio/*.idx` / `*.bin`
- 更新 `eagler26-offline-meta.resources` 里每个资源的 `size` / `stored` / `sha256`
- 更新 `eagler26-build` 清单
- 重写每个 chunk 的 `data-len` / `data-sum`
- 按原始顺序写回，保留 `__eaglerOffline.take()` 脚本和 `<script>` 结构

---

## 使用方法

### 启动

```bash
python eagler26-editor.py
```

### 典型流程

#### 改 Gaius 资源

1. 点 `打开...`，选已下载的 Gaius 客户端单 HTML 构建
2. 左侧展开到 `assets/minecraft/textures/...`
3. 选中目标 PNG
4. 点 `导出选中...` 到本地
5. 用画图 / PS / Aseprite 修改
6. 点 `替换选中...`
7. 在 `输出:` 里填新路径
8. 点 `保存 / 写出`

#### 启用启动器汉化（Gaius）

1. 勾选 `启动器汉化`
2. `保存 / 写出`
3. 输出 HTML 的启动文本变为中文

#### 改一张 MC 纹理（GoldHQ）

1. 点 `打开...`，选已下载的 GoldHQ 客户端单 HTML 构建
2. 左侧展开 `__ASSETS__/assets/minecraft/textures/...`
3. 选中目标 PNG
4. 点 `导出选中...` 到本地
5. 用画图 / PS / Aseprite 修改
6. 点 `替换选中...`，选改好的 PNG
7. 点 `保存并重打包`
8. 浏览器打开输出 HTML

#### 改音效（GoldHQ）

1. 展开 `__AUDIO__/sfx-opt/...`
2. 选中目标 OGG
3. `导出选中...` → 编辑 → `替换选中...`
4. `保存并重打包`

#### 加中文字体（GoldHQ）

1. 从正版 MC jar 提取 `unifont.zip` / `unifont_jp.zip`
2. 点 `添加文件...`，路径填：
   ```
   __ASSETS__/assets/minecraft/font/unifont.zip
   __ASSETS__/assets/minecraft/font/unifont_jp.zip
   ```
3. 编辑 `__ASSETS__/assets/minecraft/font/include/unifont.json`，内容改为：
   ```json
   {
     "providers": [
       {
         "type": "unihex",
         "file": "minecraft:font/unifont_jp.zip"
       },
       {
         "type": "unihex",
         "file": "minecraft:font/unifont.zip"
       }
     ]
   }
   ```
4. `保存并重打包`

#### 添加新文件（两种格式通用）

1. 点 `添加文件...`
2. 选本地文件
3. 弹窗输入包内路径（**GoldHQ 用 `__ASSETS__/...` 前缀**）
4. `保存 / 写出`

#### 只导出备份

1. 点 `导出全部...`
2. 选空目录
3. 得到完整资源树（**GoldHQ 会带 `__ASSETS__/` 和 `__AUDIO__/` 前缀**）

---

## 支持的格式

| 格式 | 读 | 写 |
|---|---|---|
| Gaius 26.3 单文件 HTML（`embedded.vanilla`） | ✅ | ✅ |
| `GAIUSVP1` 裸包（`.gz` / `.pack`） | ✅ | ✅ |
| GoldHQ 单文件 HTML（`x-eagler26-chunk`） | ✅ | ✅ |

---

## 技术细节

### Gaius

- `embedded.vanilla` = base64 分片数组
- 拼接 → gunzip → `GAIUSVP1` 容器
- 容器 = `MAGIC (8B)` + `index_len (4B)` + `JSON 索引` + `数据区`
- 索引 = `{ 路径: [offset, length] }`

### GoldHQ

- 每个资源 = 若干 `<script type="application/x-eagler26-chunk">`
- chunk 属性：`data-r`（路径）、`data-len`（解码后长度）、`data-sum`（Fletcher 校验）
- **编码**：原始字节以 `windows-1252` 写入，遇到 `unsafe` 表里的 36 个字节（`0x00`、`0x0D`、`0x3C`、`0x3D`、`0x80`–`0x9F`）时用 `ESC` + `(BASE + index)` 转义
- **校验**：逐字节 `s1 = (s1+c) & 0xFFFFFFFF; s2 = (s2+s1) & 0xFFFFFFFF`，最终 `sum = (s1 & 0xffff) ^ (s2 << 16)`
- **meta**：`eagler26-offline-meta` 里含 `encoding`（esc/base/unsafe）、`resources`（客户端校验用）、`build`（打包信息）
- **二级**：`assets.idx` + `assets.bin` 是"JSON 索引 + 数据拼接"，`manifest.assets` + `ranges`；`audio/*.idx` + `*.bin` 同理

---

## 依赖

- Python 3.8+
- 仅标准库：`tkinter`、`gzip`、`json`、`base64`、`struct`、`re`、`os`、`sys`、`hashlib`、`threading`、`queue`、`binascii`、`traceback`

---

## 注意事项

- **本工具不附带客户端 HTML**，请自行从对应官方渠道下载
- 修改前请备份原 HTML
- 输出 HTML 与输入 HTML 不要同名，避免覆盖
- **GoldHQ 客户端要求 chunk 顺序与 `take()` 脚本严格对应，本工具已按原顺序保留**
- **GoldHQ 的 `eagler26-offline-meta.resources` 与 `eagler26-build` 会同步更新**，否则客户端启动报 `incomplete`
- **不要用会改 HTML 编码的编辑器打开输出文件**（Eagler26 依赖 windows-1252）
- **Gaius 只读顶层资源**；**Eagler26 额外支持 `__ASSETS__/` 和 `__AUDIO__/` 二级**
- **中文字体**：Eagler26 默认不带 unifont，需要自己加（见上文"加中文字体"）
- **wasm 里的字符串**：本工具不处理，需要反编译 wasm 单独改
- **客户端更新后格式可能变化**，若工具失效请反馈新版清单

---

## 许可

本项目使用 **GPL-3.0** 许可证。详见 [LICENSE](LICENSE)。

---

## 版权声明

Copyright (C) 2026 gaius-eagler26-res-editor contributors

本程序是自由软件：你可以根据自由软件基金会发布的 GNU 通用公共许可证
（GPL）条款重新发布和/或修改它，无论是许可证的第 3 版，还是（按你的选择）
任何更新的版本。

本程序的发布是希望它有用，但不提供任何担保，甚至没有适销性或特定用途
适用性的默示担保。详见 GNU 通用公共许可证。

你应该已经收到本程序附带的 GNU 通用公共许可证副本。如果没有，请参见
https://www.gnu.org/licenses/

---

## 开发说明

本项目的部分代码与文档由 **DeepSeek AI** 辅助开发。

所有 AI 生成内容均经过人工审查、修改与验证。最终代码与文档的正确性、
安全性及合规性由项目维护者负责。

---

## 贡献

欢迎提交 Issue 和 Pull Request。

---

## 相关链接

- GNU GPL v3: https://www.gnu.org/licenses/gpl-3.0.html
- Gaius 仓库: https://github.com/TypeThe0ry/Gaius
- GoldHQ 客户端: https://client.goldhq.net/
