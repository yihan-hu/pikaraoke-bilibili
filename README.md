# MaoKaraoke（PiKaraoke Bilibili）

基于 [PiKaraoke](https://github.com/vicwomg/pikaraoke) 1.23.0（Vic Wong 及贡献者），本项目增加 Bilibili 支持。This is a modified version of PiKaraoke, not an official upstream release. 原作者版权声明保留，修改沿用 [GNU GPLv3](docs/LICENSE)，不提供担保。

## 新增功能

- 默认 Bilibili，保留 YouTube 搜索与点歌。
- B站关键词搜索、BV/av 视频链接和 b23.tv 短链接。
- 分P列表、单选/多选、按顺序分别保存或下载入队。
- 管理员扫码登录，确认后本机持久保存，重启后继续使用；支持导入 cookies。
- B站分P独立标识；兼容现有 YouTube 文件与数据库，无需数据迁移。
- MaoKaraoke 新界面：像素猫图标、霓虹屋顶夜景默认背景，手机端底部导航栏。
- 播放控制支持拖动进度条和 ±10 秒快进快退；歌词同步校准为 ±5 秒滑杆，也可直接输入秒数。

品牌素材可重新生成：`python build_scripts/brand/make_brand_assets.py`（图标、Logo）和 `python build_scripts/brand/make_background.py`（背景视频，需要 FFmpeg）。命令行和 Python 包名仍为 `pikaraoke`。

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

普通视频无需登录即可搜索。B站搜索页点击“扫码登录 / 重新登录”，用 B站 App 扫码并确认即可；登录凭据自动保存在本机，重启无需再扫，平台使登录过期时需重新登录。也可在管理员高级设置导入 Netscape cookies 文本文件。先在 PiKaraoke 设置管理员密码；未设置时沿用上游行为，局域网用户均为管理员。

cookies 保存在系统应用数据目录中的 `pikaraoke/bilibili-cookies.txt`，不在仓库内。文件等同登录凭据，请勿分享。点击“清除”可删除。过期请重新导入；可用画质受账号权限及现有高画质设置限制。

搜索结果分为“预览”和“点歌”两个按钮：点歌直接提交，按钮旁显示绿色成功提示，约3秒后淡出；未缓存的B站歌曲先在线播放、后台缓存。多P视频列表点歌默认第一P，其他分P在预览弹窗中选择。预览使用带声音的原生视频播放器，可暂停和拖动进度。单P视频无需额外选择，多P支持筛选、勾选、全选和逐P试听。Queue 页的“随机打乱”对当前待播列表重新排序，当前播放继续；不足两首时不可用。网络失败请在队列页重试。未提供账号权限外的内容访问能力。

## 兼容与回退

B站使用 `bilibili-BV号-p编号` 作为持久标识，沿用上游数据库 identity 字段（历史字段名 `youtube_id`），旧数据和旧文件保持兼容。B站文件名末尾包含对应标识。切换前请备份系统应用数据目录；回退时恢复原桌面快捷方式和原版虚拟环境。

## 开发验证

```sh
python -m pytest tests/unit -q
```

测试外部网络时需有效网络与账号；自动测试不包含真实账号 cookies。仅下载和使用你有权使用的内容。

## 实网验证

已验证无需账号的 B站搜索（10条结果）和多P视频详情（101个分P）。修复了直接调用搜索而未建立访客会话导致的 HTTP 412。扫码二维码生成与状态轮询使用 B站登录接口，成功后仅在本机保存账号凭据；已验证实际扫码登录成功，并由新的 Python 进程重新读取、确认登录仍有效。平台风控、账号过期和地区限制仍可能影响访问。

## 播放与缓存策略

B站点歌立即进入播放列表：轮到播放时优先使用已经下载好的本地文件；缓存未就绪则在 PiKaraoke 主播放器中在线播放，并保留自动切歌、暂停、音量和播放历史。后台默认2路并发缓存，Queue 页分别显示每首下载进度。YouTube 延续先下载再播放，多个下载按请求顺序交给播放列表。

在线 B站歌曲使用原调；缓存完成后可使用本地文件变调。流媒体仍需要可用网络、账号权限和可用的 B站媒体地址，缓存失败会出现在 Queue 的错误区，不妨碍已成功开始的在线播放。

Windows 桌面自定义双屏启动脚本已增加检测已有服务的逻辑：服务运行时重新打开窗口，避免重复绑定端口。开发重载时请刷新原页面，正式点歌地址为 http://localhost:5555/search，局域网设备使用主机显示的IP地址。

## 缓冲、歌词和弹幕

在线缓冲时会保留当前歌曲和播放进度，直到手动切歌或真正播完。后台缓存完成后，如果在线版本正在缓冲，会从同一进度切到本地文件。播放器区分每首歌的开始/结束事件，仅主播放屏发出结束事件，其余播放屏静音；背景伴奏在加载和播放歌曲时立即停止。歌曲结束后的随机打分默认关闭。

Download Queue 中等待和正在下载的任务都可取消；取消缓存不会移除已经点入播放列表的在线歌曲。

在首页 Player Control 的“歌词与弹幕”面板中按歌名/歌手搜索歌词。歌词来源为 [LRCLIB](https://lrclib.net/docs)。选择结果后可切换同步高亮和全文模式，并可校准时间、切换上下位置；普通歌词整首分栏显示，按屏幕自动调整字号，完整保留每一行。找不到结果时可以粘贴普通歌词或 LRC。歌词只关联当前歌曲，换歌时不会串到下一首。

用户可发送从右向左飘过的弹幕：正文大字，发送者名字在第二行小字。正文最多80字，每位用户发送间隔2秒；管理员可开关弹幕或清屏。所有字幕文字都按纯文本显示。
