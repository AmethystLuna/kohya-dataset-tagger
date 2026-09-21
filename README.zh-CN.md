<h1 align="center">Kohya Dataset Tagger</h1>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml"><img src="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
</p>

一个面向 **kohya 风格训练集**的独立标注器。LoRA、全量微调、DreamBooth 用的是同一套 `image_dir` + `.txt` sidecar 结构，整条链路都在浏览器里完成：

**浏览目录 → 画廊预览 → 逐图编辑 tag → 按 tag 过滤 → WD14 批量打标 → 生成可直接训练的 `dataset.toml`**

它是 **Anima-Standalone-Trainer** 的配套工具，而不是插件：自己的仓库、自己的进程、自己的页面，除了文件系统契约（数据集目录、`dataset.toml`、训练缓存）之外与训练器不共享任何东西。

## 状态

**V0.1 · P0 完成**。后端、前端、tagger、模型下载、`dataset.toml` 生成全部落地，并通过了 P0 验收。

```text
pytest test/ -q -n auto -m "not network"   1079 passed, 31 skipped   # 全新克隆：用仓库自带样例数据集，未装 tagger 模型
pytest test/test_docs_index.py -q          15 passed
```

数据来自 CI（`windows-latest`、Python 3.10，2026-09-21）。前提不存在的判据——真实训练集、已下载的模型、真实词表——会自己跳过并说明原因，所以全新克隆是绿的。验收套件是独立编写的那一套，由训练器自己的 `config_util` 打分；它同时需要真实数据集和训练器检出，因此只是本地闸门。CI 跑什么见 [CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

## 训练器不会告诉你的两件事

这两种失败都不报错，每一种都浪费一次训练。

**① 写 caption 不会重建文本编码器缓存。** 训练器会继续拿旧 tag 训练，什么都不说。所以本工具要保证的不是「编辑」，而是缓存：每次写入 `.txt` 都会同时删掉配对的 `cache_text_encoder/<name>_anima_te.safetensors`，删不掉就报错，而不是把它吞掉。证据与复盘：[.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md)（英文）。

**② 训练器枚举图片时不递归。** `glob_images()` 只扫一层，`image_dir` 指向父目录时训练集就是空的。真实训练集有几百个子目录，所以本工具按**一个目录一个 subset** 生成，并由训练器自己的配置校验器验收（[tools/verify_toml_with_trainer.py](tools/verify_toml_with_trainer.py)）。

顺带它还会报告六类训练器会默默接受的问题：缺 caption、caption 里含 `\,`（会被读成两个 tag）、图片大于 `max_bucket_reso`、图片带 alpha、图片太少、空目录。

## 安装与运行

| 要做什么 | Windows | Linux / macOS |
|---|---|---|
| 第一次：创建环境 | `setup_env.bat` | `./setup_env.sh` |
| 每次：启动并打开浏览器 | `start.bat` | `./start.sh` |
| 同样的两件事，走国内镜像 | `setup_env_cn.bat` | `./setup_env_cn.sh` |

打开页面之前，启动器会

- 没有 `.venv` 就先问你要不要初始化；
- 第一次问你一次数据集根目录，存进 `roots.txt`（已 gitignore），以后不再问；
- 端口被占用就换下一个（3001 → 3002 → …）；
- 服务真的应答之后才打开浏览器，所以不会出现打不开的页面；
- 在前台运行，Ctrl+C 结束。

两套启动器都接受 `--dry-run`：只打印命令行，什么都不启动。

也可以不用启动器：

```powershell
.\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3001
.\scripts\start.ps1 -NoBrowser
```

环境脚本有自己的开关：

```powershell
.\setup_env.bat -Gpu cuda        # 用 CUDA EP（需要 torch 或 nvidia 运行时）
.\setup_env.bat -Gpu cpu
.\setup_env.bat -Index cn        # 国内镜像档（等价于 setup_env_cn.bat）
.\setup_env.bat -DryRun          # 只打印会执行的选择，不装任何东西
.\setup_env.bat -Recreate        # 重建 venv（先打印绝对路径并征求确认）
```

唯一的依赖是本仓库自己的 `.venv`（约 250 MB）。**不需要 torch**，也不要往训练器的 venv 里装任何东西。

**中国大陆网络**用 `setup_env_cn.*`：同一份实现，只是包源不同。它按 `USTC → 阿里云 → pypi.org` 的顺序尝试，前一个失败才换下一个，所以某个镜像临时挂了也装得上。2026-09-19 拉取 245 MB 的 `onnxruntime-gpu` 时实测：USTC 8.4–11 MB/s，阿里云 0.86–1.25 MB/s，pypi.org 0.55–1.01 MB/s。想换别的镜像就设 `KOHYA_TAGGER_PIP_INDEX=https://your/simple`（多个用逗号分隔，按顺序尝试）。

## GPU

速度由 onnxruntime 决定，所以 Windows 的安装脚本默认装 **DirectML** 版：50 MB、自带、不挑厂商，不需要 CUDA 或 cuDNN。在参考机器上实测（RTX 4070 Ti SUPER，16 张图，预热后取中位数）：

| 提供程序 | 每张图 | 1653 张的数据集 |
|---|---|---|
| CPU | 0.721 秒 | 19.9 分钟 |
| **DirectML**（默认） | **0.192 秒** | **5.3 分钟** |
| CUDA | 0.168 秒 | 4.6 分钟 |

CUDA 快 14%，代价是多 195 MB 外加一个 torch 依赖，所以它不是 Windows 上的默认值。

装 onnxruntime 必须钉住 `--index-url`（两个安装脚本都已经这么做了）：全局 pip 源可能慢到看起来像卡死——参考机器上实测 0.07 MB/s。装完确认实际拿到的是哪个，`get_available_providers()` 不算数：

```powershell
.\.venv\Scripts\python.exe tools\check_provider.py    # 用真实模型建一个 session，报告实际生效的提供程序
```

提供程序加载失败时 onnxruntime 会**静默回退到 CPU**：推理照跑，慢 4 倍。

## Tagger 模型

选中的模型本地没有就自动下载，依次尝试 `KOHYA_TAGGER_HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com`（国内镜像，其清单已验证与主站一致）。

搜索根按下面的顺序使用，同名模型取最前面的：

1. 本仓库的 `models/`——第一个搜索根，也是默认下载目的地
2. 你自己加的目录（`model_paths.txt`，然后是 `--extra-models` / `KOHYA_TAGGER_EXTRA_MODELS`）
3. `%LOCALAPPDATA%/kohya-dataset-tagger/models`——程序自己的下载目录
4. HuggingFace 缓存，排在最后

**最省事的加目录办法是在界面里**：Tagger 面板 →「模型目录…」→ 粘贴绝对路径。加进去立即生效，不用重启，并写回 `model_paths.txt`。另外三种等价做法：

```powershell
# 1. 编辑 model_paths.txt（一行一个路径；# 开头是注释，分号也可以分隔）——启动时读取
# 2. 环境变量（追加，不替换）：
set KOHYA_TAGGER_EXTRA_MODELS=D:\my-taggers;E:\more
# 3. 直接跑 CLI（启动器没有 -ExtraModels 参数）：
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --extra-models "D:\my-taggers"
```

> `scripts\start.ps1` / `scripts\start.sh` 只做「挑端口 + 读 roots.txt + 起服务」，不转发模型目录（§3.7）。一旦变成命令行参数，你在界面里删掉的一行下次启动又会被推回来。

`--models` / `KOHYA_TAGGER_MODELS` 是另一回事：它**替换整个搜索列表**，会丢掉仓库的 `models/` 和所有自动发现的位置。「再加一个目录」要的是上面的追加。

默认列表里没有任何只存在于某台机器的路径。你的 webui / ComfyUI 模型放在哪，就把那个目录写进 `model_paths.txt`。

**下载不通也不是死路**：把同一个仓库的 `model.onnx` 和 `*.csv` 放进

```text
models/<model-id>/
```

这个目录里已经有一个占位文件，文件名说的就是这件事。它既是第一个搜索根，也是默认下载目的地，所以手放的和自动下载的在同一处。

## 切换数据集 / 添加模型目录：不用重启

这两类路径以前只能用命令行参数或配置文件设置，而且只在启动时读一次。现在都能在界面里改，立即生效：

| 你想做什么 | 在哪里 | 效果 |
|---|---|---|
| 切换或添加数据集目录 | 左栏「根目录」标题旁的 **＋** | 立刻可浏览；写回 `roots.txt` |
| 删除数据集目录 | 每个根目录后面的「删除」 | 最后一个删不掉（一个都不剩就打不开任何东西） |
| 添加模型搜索目录 | Tagger 面板 →「模型目录…」 | 立刻出现在模型列表里；写回 `model_paths.txt` |
| 删除模型搜索目录 | 同一个对话框里每项的「移除」 | 自动发现的那些只在本次运行生效，界面会说明 |

浏览器给不了原生目录选择器，所以**请粘贴绝对路径**。（为了这一个控件开一个「列出任意目录」的端点，等于废掉白名单。）目录必须已经存在：配一个不存在的根，只会让每个请求都 403 或 404。

写文件失败时功能**照样能用**，但界面会直接说「重启后这条会丢」，而不是假装保存成功。

## 开发

先读 [AGENTS.md](AGENTS.md)（英文）——它是指向 `.github/memory/` 的常驻索引。工作流程在 [.agents/skills/kohya-dataset-tagger-workflow/SKILL.md](.agents/skills/kohya-dataset-tagger-workflow/SKILL.md)（英文）。

```powershell
.\.venv\Scripts\python.exe tools\check_relevant.py          # 内环：只跑覆盖本次改动的测试
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # 汇报前的闸门：全量套件 + 文档闸门
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto       # 全量单元套件
.\.venv\Scripts\python.exe -m pytest acceptance/ -q         # 验收判据（需要真实数据集）
```

### 测试从哪里取数据

`local_paths.ini` 是一个检出找到自己的训练集、模型和训练器的地方；随仓库提交的模板 `local_paths.ini.example` 指向 `test/fixtures/sample_dataset/` 下的样例数据集，所以套件开箱即跑。每个键的含义见 [CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

### 验收

`pytest acceptance/` 里是照着规格独立写、独立于实现的判据。A23 由真实消费方打分：把生成的 `dataset.toml` 交给训练器自己的 `config_util`，要求它按目录建出 subset。这需要真实数据集和训练器检出，所以是本地闸门。

### 文档结构

| 路径 | 是什么 |
|---|---|
| `AGENTS.md` | 常驻索引（≤ 28 KB，由闸门强制） |
| `.github/memory/MEMORY.md` | 分类表 |
| `.github/memory/INDEX-<category>.md` | 每个分类一份主题清单 |
| `.github/memory/<topic>.md` | 正文 |
| `.github/memory/p0-spec.md` | P0 冻结契约与验收判据 |
| `.agents/skills/<name>/SKILL.md` | 工作流程 |

`test/test_docs_index.py` 防止这套结构烂掉：预算、链接可达、没有孤儿主题、skill 元数据合规，以及三份 README 保持同步。

## 参与贡献

欢迎提 issue 和 PR，中英文都可以。[CONTRIBUTING.md](CONTRIBUTING.md)（英文）里有环境搭建、两级闸门、改动必须遵守的规则，以及 CI 跑什么。安全问题请走 [SECURITY.md](SECURITY.md)（英文）；项目遵循 [Contributor Covenant](CODE_OF_CONDUCT.md)（英文）。

## 许可证

[MIT](LICENSE) © 2026 AmethystLuna。
