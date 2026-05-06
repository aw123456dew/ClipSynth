# FrameCut - 智能视频剪辑工具

FrameCut 是一款基于 PySide6 开发的智能视频剪辑桌面应用，集成 AI 分析、字幕处理和剪映草稿导出功能，专为短视频创作者设计。

## 🎯 项目功能简介

### 核心功能

1. **智能视频剪辑**
   - 基于 AI 的视频内容分析
   - 自动识别精彩片段
   - 支持多种分析模式（极速/快速/精准/深度）

2. **短剧解说制作**
   - 自动生成解说文案
   - 集成 AI 语音合成（支持豆包 TTS）
   - 智能匹配视频片段与配音时长

3. **字幕处理**
   - 字幕导入与编辑
   - 字幕与视频同步
   - 支持 SRT 格式导出

4. **剪映草稿导出**
   - 直接导出为剪映草稿文件
   - 保留完整的视频轨道、音频轨道和字幕轨道
   - 支持批量导出多个片段

5. **视频去重工具**
   - 检测相似视频片段
   - 智能去重处理

### 技术栈

- **框架**: PySide6 (Qt6)
- **异步**: qasync + asyncio
- **数据库**: SQLite + SQLAlchemy
- **AI 服务**: OpenAI API + 豆包 API
- **媒体处理**: FFmpeg + OpenCV + PIL
- **打包工具**: PyInstaller

## 🚀 项目如何运行

### 环境要求

- Python 3.12+
- FFmpeg（需提前安装并配置环境变量）
- Windows 10/11（推荐）

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd FrameCut
```

2. **创建虚拟环境**
```bash
python -m venv venv
venv\Scripts\activate  # Windows
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **配置环境变量（可选）**
```bash
# 设置 OpenAI API Key（如需使用 AI 功能）
set OPENAI_API_KEY=your-api-key

# 设置豆包 API Key（如需使用 TTS 功能）
set DOUBAO_API_KEY=your-api-key
set DOUBAO_SECRET_KEY=your-secret-key
```

### 运行项目

```bash
python main.py
```

## 📦 项目如何打包

### 打包要求

- 需提前安装 PyInstaller
- FFmpeg 需放置在指定路径：`D:\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build`

### 打包步骤

1. **安装打包依赖**
```bash
pip install pyinstaller
```

2. **运行打包脚本**
```bash
python scripts/build_exe.py
```

3. **打包选项**
```bash
# 清理构建缓存并打包
python scripts/build_exe.py --clean

# 打包带控制台窗口（用于调试）
python scripts/build_exe.py --console

# 完整命令示例
python scripts/build_exe.py --clean --console
```

### 打包输出

打包完成后，可执行文件位于：
```
dist/ClipSynth/ClipSynth.exe
```

### 注意事项

1. **FFmpeg 配置**：打包脚本会自动复制本地 FFmpeg 到输出目录
2. **资源文件**：所有资源文件（样式、图标等）会自动打包
3. **运行时环境**：打包后的 exe 可独立运行，无需安装 Python

## 📁 项目结构

```
FrameCut/
├── clip_synth/              # 主应用代码
│   ├── core/               # 核心模块（应用、配置、数据库）
│   ├── models/             # 数据模型
│   ├── services/           # 业务服务（AI、媒体处理、导出）
│   ├── ui/                 # 用户界面（页面、组件、窗口）
│   ├── tools/              # 工具模块（去重工具）
│   └── utils/              # 通用工具（FFmpeg 辅助、日志等）
├── scripts/                # 脚本（打包、运行时钩子）
├── tests/                  # 测试用例
├── main.py                 # 应用入口
├── requirements.txt        # 依赖清单
└── pyproject.toml          # 项目配置
```

## ⚙️ 配置说明

### FFmpeg 路径配置

默认 FFmpeg 路径：
```
D:\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build
```

如需修改路径，请编辑 `scripts/build_exe.py` 中的相关配置。

### 运行时环境变量

应用启动时会自动将打包的 FFmpeg 添加到系统 PATH，无需手动配置。

## 📝 使用说明

### 基本工作流程

1. **创建项目** → 选择视频文件
2. **分析视频** → 使用 AI 分析精彩片段
3. **编辑剪辑** → 调整片段顺序和时长
4. **添加字幕** → 导入或生成字幕
5. **导出作品** → 导出为视频或剪映草稿

### 快捷键

- `Ctrl + S`：保存项目
- `Ctrl + E`：导出项目
- `Ctrl + Z`：撤销操作

## 🐛 常见问题

### Q: 运行时提示 FFmpeg 找不到？
A: 请确保 FFmpeg 已正确安装并配置环境变量，或检查打包脚本中的 FFmpeg 路径配置。

### Q: 导出剪映草稿失败？
A: 请确保视频文件路径不含中文或特殊字符，且存储空间充足。

### Q: AI 分析速度慢？
A: 可切换为「极速模式」，仅使用字幕分析，不分析画面内容。

## 📄 许可证

**版权所有 © 2024 FrameCut 开发团队**

本项目为自有项目，仅供内部使用和学习研究。**严禁用于任何商业目的**，未经授权禁止复制、分发或修改本项目的任何部分。

违反上述条款将依法追究法律责任。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！