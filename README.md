# PiKaraoke Bilibili

基于 [PiKaraoke](https://github.com/vicwomg/pikaraoke) 1.23.0（Vic Wong 及贡献者），本项目增加 Bilibili 支持。This is a modified version of PiKaraoke, not an official upstream release. 原作者版权声明保留，修改沿用 [GNU GPLv3](docs/LICENSE)，不提供担保。

## 新增功能

- 默认 Bilibili，保留 YouTube 搜索与点歌。
- B站关键词搜索、BV/av 视频链接和 b23.tv 短链接。
- 分P列表、单选/多选、按顺序分别保存或下载入队。
- 管理员导入、检查、清除 B站 Netscape cookies。
- B站分P独立标识；兼容现有 YouTube 文件与数据库，无需数据迁移。

## 安装和运行

需要 Python 3.10+、FFmpeg 和 Chromium 浏览器。系统依赖见 [上游说明](docs/README.md)。

```sh
git clone https://github.com/yihan-hu/pikaraoke-bilibili.git
cd pikaraoke-bilibili
uv venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
.venv/Scripts/pikaraoke.exe
```

Linux/macOS 使用 `.venv/bin/python` 和 `.venv/bin/pikaraoke`。访问 http://localhost:5555/search，默认搜索 B站，页面可切换 YouTube。

## B站登录与分P

在浏览器登录 B站后，导出 Netscape cookies 文本文件。在 B站搜索页展开管理员登录设置，导入文件后点击“检查登录”。先在 PiKaraoke 设置管理员密码；未设置时沿用上游行为，局域网用户均为管理员。

cookies 保存在系统应用数据目录中的 `pikaraoke/bilibili-cookies.txt`，不在仓库内。文件等同登录凭据，请勿分享。点击“清除”可删除。过期请重新导入；可用画质受账号权限及现有高画质设置限制。

搜索结果点击“选择 / 点歌”，或粘贴链接读取视频。默认勾选链接指定P（否则第一P），可多选或全选后保存/入队。每个分P作为一首歌曲。网络失败请在队列页重试；嵌入预览受限时使用原站入口。首版登录不包含扫码，未提供账号权限外的内容访问能力。

## 兼容与回退

B站使用 `bilibili-BV号-p编号` 作为持久标识，沿用上游数据库 identity 字段（历史字段名 `youtube_id`），旧数据和旧文件保持兼容。B站文件名末尾包含对应标识。切换前请备份系统应用数据目录；回退时恢复原桌面快捷方式和原版虚拟环境。

## 开发验证

```sh
python -m pytest tests/unit -q
```

测试外部网络时需有效网络与账号；自动测试不包含真实账号 cookies。仅下载和使用你有权使用的内容。

## 当前验证限制

当前网络下 B站匿名搜索返回 HTTP 412；需要导入有效 cookies 后进行账号实测，cookies 也不保证消除平台风控。两站页面和双屏启动检查已通过。真实登录、下载及有声播放尚待账号环境验证。
