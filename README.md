# gaius-res-editor

A tool to browse, modify, and inject resources into Gaius WebGL2 Minecraft builds.

浏览、修改、注入 [Gaius WebGL2 Minecraft](https://github.com/TypeThe0ry/Gaius) 资源包。

---

## 简介

`gaius-res-editor` 是一个用于编辑 [Gaius](https://github.com/TypeThe0ry/Gaius) 单文件 HTML 中嵌入式资源包的工具。它可以解析 `embedded.vanilla` 数组与 `GAIUSVP1` 容器，让你像操作普通资源包一样浏览、修改、注入资源，并可选地对启动器文本进行汉化。

- 纯 Python 标准库实现，无第三方依赖
- 同时提供 GUI 与 CLI
- 支持单文件级 添加 / 替换 / 重命名 / 删除
- 支持导出单个 / 全部 / 裸包
- 支持写回 HTML 或裸 `GAIUSVP1` 包
- 可选启动器文本汉化

---

## 功能介绍

### 解析

- 从 [Gaius](https://github.com/TypeThe0ry/Gaius) 单文件 HTML 中提取 `embedded.vanilla` 的 base64 分片
- 拼接、gunzip、解析 `GAIUSVP1` 容器
- 读取 JSON 索引与数据区
- 也支持直接打开裸包（`.gz` / `.pack` / `GAIUSVP1`）

### 浏览

- 按目录树显示资源条目
- 按关键词过滤路径
- 显示每个条目的大小
- 预览文本 / JSON（自动美化）/ 二进制（十六进制）

### 修改

| 操作 | 说明 |
|---|---|
| 添加 | 把本地文件加入包内，指定目标路径 |
| 替换 | 覆盖已有条目，支持原地覆盖与追加 |
| 重命名 | 改索引 key，可用 `/` 实现移动 |
| 删除 | 从索引移除，保存时自动 compact 回收空间 |

### 导出

- 导出选中条目
- 导出全部条目（保留目录结构）
- 导出裸 `GAIUSVP1` 包（`.gz`）

### 写回

- 写回 HTML：重建 `GAIUSVP1` → gzip → base64 分片 → 替换 `vanilla` 数组
- 写回裸包：直接输出 gzip 后的 `GAIUSVP1`
- 可选：保存 HTML 时自动汉化启动器文本

### 启动器汉化

- 覆盖初始状态、进度文案、状态文案、错误提示、外壳 UI、按钮文案
- 仅对 HTML 输出生效；导出裸包时此选项无效
- 可在 GUI 里用复选框开关

### 容器处理

- `GAIUSVP1` 索引 + 数据区解析
- 原地覆盖优化（新数据 ≤ 旧数据时不增长）
- 保存时自动 compact，回收删除/覆盖产生的垃圾
- 校验索引 offset/length 是否越界

---

## GUI 使用方法

### 启动

```bash
python gaius_res_editor.py
```

### 界面结构

```
┌─ 1. 文件 ─────────────────────────────────────────┐
│ 输入: [____________________] [打开...]            │
│ 输出: [____________________] [另存为...]          │
└───────────────────────────────────────────────────┘
┌─ 2. 启动器汉化 ───────────────────────────────────┐
│ [ ] 注入后汉化启动器（仅对 HTML 输出生效）        │
└───────────────────────────────────────────────────┘
┌─ 3. 包信息 ───────────────────────────────────────┐
│ 格式: Gaius HTML  条目数: 12345  数据区: 57.42 MB │
└───────────────────────────────────────────────────┘
┌─ 4. 条目 ──────────────┐┌─ 5. 预览 ─────────────┐
│ 过滤: [____] [清空]    ││ // assets/...         │
│ ▸ assets/             ││ // 1234 字节          │
│   ▸ minecraft/        ││ ============          │
│     ▸ textures/       ││ { ... }               │
│       widgets.png 1.2K││                       │
└───────────────────────┘└───────────────────────┘
┌─ 6. 操作 ─────────────────────────────────────────┐
│ [添加文件] [替换选中] [重命名] [删除]              │
│ [导出选中] [导出全部] [预览]                       │
│ [保存/写出] [导出裸包]                             │
└───────────────────────────────────────────────────┘
┌─ 7. 日志 ─────────────────────────────────────────┐
│ [✓] 已加载: Gaius.html                            │
│ [✓] 条目数: 12345                                  │
└───────────────────────────────────────────────────┘
```

### 典型流程

#### 改一张贴图

1. 点 `打开...`，选 `Gaius.html`
2. 左侧展开到 `assets/minecraft/textures/...`
3. 选中目标 PNG
4. 点 `导出选中...`，存到本地
5. 用画图 / PS / Aseprite 修改
6. 点 `替换选中...`，选改好的 PNG
7. 在 `输出:` 里填新路径
8. 点 `保存 / 写出`

#### 改语言

1. 过滤框里搜 `zh_cn.json`
2. 选中，点 `预览` 看内容
3. `导出选中...` 到本地
4. 用编辑器改 JSON
5. `替换选中...`
6. `保存 / 写出`

#### 添加新文件

1. 点 `添加文件...`
2. 选本地文件
3. 弹窗输入包内路径，例如：
   ```
   assets/minecraft/lang/zh_cn.json
   ```
4. `保存 / 写出`

#### 重命名 / 移动

1. 选中条目
2. 点 `重命名...`
3. 输入新路径，`/` 分隔实现移动：
   ```
   assets/minecraft/lang/zh_cn_old.json
   ```

#### 删除

1. Ctrl / Shift 多选
2. 点 `删除`
3. `保存 / 写出`

#### 启用启动器汉化

1. 勾选 `注入后汉化启动器`
2. `保存 / 写出`
3. 输出 HTML 的启动文本变为中文
4. 不勾选则保持英文

#### 只导出备份

1. 点 `导出全部...`
2. 选空目录
3. 得到完整资源树

---

## CLI 使用方法

### 启动

```bash
python gaius_res_editor.py <输入文件>
```

或带参数：

```bash
python gaius_res_editor.py <输入文件> [选项]
```

### 命令行参数

| 参数 | 说明 |
|---|---|
| `input` | 输入 HTML 或 GAIUSVP1 裸包 |
| `--out`, `-o` | 直接保存到指定输出（不进入菜单） |
| `--extract-all`, `-x DIR` | 解包全部到目录 |
| `--list`, `-l` | 列出所有条目 |
| `--info`, `-i` | 只显示信息 |

### 示例

查看信息：

```bash
python gaius_res_editor.py Gaius.html --info
```

列出条目：

```bash
python gaius_res_editor.py Gaius.html --list
```

全部导出：

```bash
python gaius_res_editor.py Gaius.html --extract-all vanilla_out
```

直接保存：

```bash
python gaius_res_editor.py Gaius.html --out Gaius_modified.html
```

### 交互式菜单

不带 `--out` / `--list` / `--info` / `--extract-all` 时，进入交互式菜单：

```bash
python gaius_res_editor.py Gaius.html
```

可用命令：

| 命令 | 说明 |
|---|---|
| `info` | 显示包信息 |
| `list [正则]` | 列出条目 |
| `search <关键词>` | 搜索路径 |
| `extract-one <路径> [输出]` | 导出单个文件 |
| `extract-all [目录]` | 导出全部 |
| `add <本地文件> <包内路径>` | 添加新文件 |
| `replace <本地文件> <包内路径>` | 覆盖已有文件 |
| `rename <旧路径> <新路径>` | 重命名 |
| `delete <路径>` | 删除 |
| `save [输出路径]` | 保存 |
| `export-raw [输出路径]` | 导出裸包 |
| `help` | 显示帮助 |
| `quit` | 退出 |

交互式示例：

```
gaius> search lang
gaius> extract-one assets/minecraft/lang/zh_cn.json zh_cn.json
gaius> replace zh_cn.json assets/minecraft/lang/zh_cn.json
gaius> save Gaius_modified.html
```

---

## 支持的格式

- Gaius 26.3 单文件 HTML（`embedded.vanilla` 数组）
- `GAIUSVP1` 裸包（`.gz` / `.pack`）
- 可写回上述两种格式

---

## 依赖

- Python 3.8+
- 仅使用标准库：`tkinter`、`gzip`、`json`、`base64`、`struct`、`zlib`、`re`、`os`、`sys`、`binascii`、`traceback`

---

## 注意事项

- 修改前请备份原 HTML
- 输出 HTML 与输入 HTML 不要同名，避免覆盖
- 保存 HTML 后建议加 `?fresh=1` 启动，避免浏览器缓存旧资源
- 如果只改语言，不需要清 shader 缓存
- 如果改了 `shaders/` 下的 GLSL，需要清 IndexedDB 的 `gaius-shader-cache-v1-26.3`
- Gaius 源代码仓库: https://github.com/TypeThe0ry/Gaius

---

## 许可

本项目使用 **GPL-3.0** 许可证。详见 [LICENSE](LICENSE)。

---

## 版权声明

Copyright (C) 2026 gaius-res-editor contributors

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
- 项目仓库: https://github.com/Enchantment-Niko/gaius-res-editor