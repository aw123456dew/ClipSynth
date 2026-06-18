# AI推 - AI Video Editor

这是一款完全由AI实现的项目
主要功能 基于 AI 能力的视频编辑与漫画生成工具，集成了视频智能剪辑、AI 语音解说、视频配音混剪、漫画自动生成等功能。

---

## 功能总览

| 功能模块 | 说明 |
|---------|------|
| [短剧混剪](docs/short_drama_mix.md) | 基于智能分析对视频进行自动剪辑和混剪 |
| [视频解说 V2](docs/short_drama_narrate_v2.md) | 新一代 AI 视频解说生成，支持角色识别与配音 |
| [视频解说](docs/short_drama_narrate.md) | AI 自动生成视频解说脚本并配音 |
| [视频处理](docs/video_dedup.md) | 去重、预处理等视频处理工具 |
| [视频配音混剪](docs/novel_mix.md) | 小说文本转视频配音与混剪 |
| [漫画生成](docs/novel_comic.md) | 小说文本自动生成漫画分镜与图片 |
| [系统配置](docs/settings.md) | AI 模型、API 密钥等系统设置 |

---

## 项目架构

```
FrameCut/
├── clip_synth/                  # 核心代码
│   ├── core/                    # 核心基础设施
│   │   ├── application.py       # 应用入口与初始化
│   │   ├── config.py            # 配置管理
│   │   └── database.py          # 数据库管理
│   ├── models/                  # 数据模型
│   │   ├── project.py           # 项目模型
│   │   ├── project_state.py     # 项目状态
│   │   ├── settings.py          # 系统设置模型
│   │   └── ...                  # 各类业务模型
│   ├── services/                # 业务服务层
│   │   ├── ai_service.py        # AI 对话与图片生成
│   │   ├── image_gen_service.py # 图片生成队列服务
│   │   ├── video_analysis_service.py  # 视频分析
│   │   ├── subtitle_service.py  # 字幕处理
│   │   ├── novel_comic_state_service.py  # 漫画状态管理
│   │   └── ...                  # 其他业务服务
│   ├── ui/                      # 用户界面
│   │   ├── pages/               # 功能页面
│   │   ├── components/          # 通用组件
│   │   ├── widgets/             # 界面控件
│   │   ├── main_window.py       # 主窗口
│   │   └── login_dialog.py      # 登录对话框
│   ├── resources/               # 资源文件
│   │   ├── styles/main.qss      # 全局样式表
│   │   └── icons/               # 图标
│   └── utils/                   # 工具函数
│       ├── ffmpeg_helper.py     # FFmpeg 工具
│       ├── gpu_accel.py         # GPU 加速
│       └── logger.py            # 日志
├── scripts/
│   └── build_exe.py             # 打包脚本 (PyInstaller)
├── main.py                      # 程序入口
├── pyproject.toml               # 项目配置
└── requirements.txt             # Python 依赖
```

### 架构分层

```
┌─────────────────────────────────────────┐
│               UI 层 (PySide6)            │
│  ┌─────────┐ ┌───────────┐ ┌─────────┐  │
│  │ 功能页面 │ │  通用组件  │ │  控件   │  │
│  └────┬────┘ └───────────┘ └─────────┘  │
├───────┼─────────────────────────────────┤
│       ▼                                  │
│  ┌──────────────────────┐               │
│  │    业务服务层         │               │
│  │  AI服务 / 图片生成    │               │
│  │  视频分析 / 状态管理  │               │
│  └──────────┬───────────┘               │
├─────────────┼───────────────────────────┤
│             ▼                            │
│  ┌──────────────────┐ ┌──────────────┐  │
│  │   数据模型层      │ │  数据库/配置  │  │
│  └──────────────────┘ └──────────────┘  │
└─────────────────────────────────────────┘
```

---

## 环境要求

- **Python**: 3.10+
- **操作系统**: Windows 10/11
- **FFmpeg**: 需在环境变量中或随打包分发
- **GPU**: 非必需，视频编码推荐使用 NVIDIA GPU

---

## 如何运行

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/FrameCut.git
cd FrameCut
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 FFmpeg

确保 `ffmpeg.exe` 和 `ffprobe.exe` 在系统 PATH 中，或放在项目根目录下的 `ffmpeg/` 文件夹中。

### 4. 启动程序

```bash
python main.py
```

### 5. 系统配置

启动后进入「系统配置」页面，配置以下信息：

- **文案生成模型**: OpenAI 兼容的文本模型（API Key + 接口地址）
- **视频分析模型**: 用于视频分析的视觉模型
- **图片生成模型**: 用于漫画/图片生成的模型，可选 NewAPI / ToAPI / Grasai / JiKe / ManXiaoBai 等供应商

---

## 如何打包

使用 PyInstaller 打包为单个 exe 文件：

```bash
# 基础打包（隐藏控制台窗口）
python scripts/build_exe.py

# 带控制台（调试用）
python scripts/build_exe.py --console

# 自定义名称
python scripts/build_exe.py --name AI推

# 清理缓存后打包
python scripts/build_exe.py --clean
```

打包完成后，exe 文件位于 `dist/` 目录下。

### 打包包含

- 所有 Python 依赖
- FFmpeg (ffmpeg.exe + ffprobe.exe)
- QSS 样式文件
- 图标资源
- pyJianYingDraft 资产文件

---

## 技术栈

| 技术 | 用途 |
|------|------|
| PySide6 | GUI 框架 |
| OpenAI API | AI 对话与图片生成 |
| FFmpeg | 视频/音频处理 |
| PyInstaller | 应用打包 |
| SQLite | 本地数据存储 |
| httpx / aiohttp | HTTP 请求 |

---

## 常见问题

**Q: 启动后界面空白？**  
A: 检查 `clip_synth/resources/styles/main.qss` 是否存在。

**Q: AI 生图失败？**  
A: 进入「系统配置」确认图片生成模型的 API Key 和接口地址是否正确，并点击"测试生图"验证。

**Q: 打包后运行报错？**  
A: 使用 `--console` 参数重新打包，查看具体错误信息。
