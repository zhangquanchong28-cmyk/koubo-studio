# 口播工作台（koubo-studio）

用户是中文口播视频创作者，主要做**竖屏**口播，单条一般 5 分钟以内、原片 3GB 以内。
这个仓库同时是：
1. **云端剪辑工作室**：用户在 claude.ai 里把视频发给你，你在云端用 `koubo.py` 剪好交付。**这是主要用法。**
2. **本地 Windows 工作台**（`start.bat` → 浏览器）：用户本地用原片出高清、导剪映草稿。

用户剪辑相关的请求（"剪这条""按我的风格剪""第 3 句删掉""这里加个卡片""记住以后……"）
→ 先读并按 `.claude/skills/koubo-edit/SKILL.md` 执行。

## 云端环境准备（新会话第一次用时）
```bash
cd <仓库目录>
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
which ffmpeg >/dev/null || apt-get install -y ffmpeg
fc-list | grep -qi "wqy" || apt-get install -y fonts-wqy-zenhei
```
- 语音模型 faster-whisper medium（约 1.5GB）第一次转写时自动下载；HF 下不动会自动换 hf-mirror.com。
- AI（方案 + 生图）走 OpenRouter。云端环境里 Key 由网络代理注入，代码里的占位符是 `proxy-injected`，
  **永远不要让用户把 Key 贴进聊天**；没配好就用 request_credential 让用户在安全表单里填（host: openrouter.ai）。
- 观看模式：用户能在 App 右侧预览窗实时看你操作，启动方式见技能文件「打开观看模式」。
- Python 走代理时如遇证书错误：`export SSL_CERT_FILE=/root/.ccr/ca-bundle.crt`（只在云端容器里需要）。

## 代码地图
| 文件 | 作用 |
|---|---|
| `koubo.py` | 云端命令行：new / plan / show / check / broll / render / frames / jianying / pack / list |
| `asr.py` | faster-whisper 逐字时间戳 → 短句；繁体自动转简体、怪标点规范化 |
| `planner.py` | Claude 出剪辑方案（JSON）；系统提示词内置晨晨方法论 + 自动拼入 `经验/我的经验.md` |
| `packer.py` | 出片引擎：剪切、推近、ASS 动态字幕、数字滚动、3D 卡片、配图叠加、音效、BGM |
| `broll.py` | 配图：Pexels / Pixabay 免费图库（可选 Key）+ OpenRouter AI 生图 |
| `jianying.py` + `windows_draft.py` | 剪映草稿导出（windows_draft 来自 video-shotcraft，Apache-2.0） |
| `concat_export.py` | 【实验，未实测】导出到开源剪辑器 Concat |
| `server.py` + `ui.html` | 本地网页工作台（127.0.0.1），项目目录格式与 koubo.py 相同 |
| `shrink.py` + `压缩代理.bat` | 本地把大原片压到 30MB 以内供上传 |
| `经验/` | 方法论与用户经验库（技能会往 `我的经验.md` 里追加） |

项目数据都在 `projects/<编号>/`（已 gitignore）：`state.json`、`transcript.json`、`plan.json`、`broll/`、`out/`。

## 开发约定
- 目标平台是 **Windows 10/11 + Python 3.12**；云端只是开发/代剪环境。路径用 pathlib，子进程别依赖 shell。
- `.bat` 必须 CRLF、只用英文提示（中文 codepage 容易乱码）；`setup.ps1`、`README.txt` 带 UTF-8 BOM（`.gitattributes` 已配好换行）。
- 不加重依赖；网络请求优先用标准库。新依赖写进 `requirements.txt`（`setup.ps1` 会装）。
- 改完至少用一条测试视频跑通 `new → plan → render → frames` 并看抽帧图再交付。
- 给用户的说明一律中文、少术语。花钱的操作（AI 方案、AI 生图）要报花费。
- 不要把用户视频、projects/、config.json 提交进仓库。
