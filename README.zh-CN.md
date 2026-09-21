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

一个独立的 **kohya 风格训练集**标注器：LoRA / 全量微调 / DreamBooth 用的都是同一套 `image_dir` + `.txt` sidecar 格式。整条链路都在浏览器里完成：

**浏览目录 → 画廊预览 → 逐图编辑 tag → 按 tag 过滤 → WD14 批量打标 → 生成可直接训练的 dataset.toml**

它是 **Anima-Standalone-Trainer**（同作者的另一个项目，未在此发布）的**配套工具**，而不是它的插件：
自己的仓库、自己的进程、自己的页面，只通过文件系统契约（数据集目录、`dataset.toml`、训练缓存）与训练器交互。

## 状态

**V0.1 · P0 完成**。后端、前端、tagger、模型下载、dataset.toml 生成全部落地，并通过了 P0 验收。

    pytest test/ -q -n auto -m "not network"   1072 passed, 31 skipped   # 全新克隆：用仓库自带样例数据集，未装 tagger 模型
    pytest test/test_docs_index.py -q          9 passed

测试需要的每一个路径都来自 `local_paths.ini`（已 gitignore，模板随仓库提交）；那些前提不存在的判据——真实训练集、已下载的 tagger 模型、真实词表——会**自己跳过并说明原因**，所以全新克隆是绿的。验收套件是独立编写的那一套，由训练器自己的 `config_util` 打分；它是本地闸门，不是对外公布的数字。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 训练器不会告诉你的两件事

这两点就是本工具存在的理由，也是它和「手写配置文件」的区别：

**① 改完 caption 之后，训练器不会重建文本编码器缓存。**

它会**悄无声息地继续用旧 tag 训练**，一个错都不报。
所以本工具的头号功能不是「编辑」，而是**改完立刻把缓存处理正确**——
每一次写入 `.txt`，都会同时删掉配对的 `cache_text_encoder/<name>_anima_te.safetensors`；
删不掉就报错，而不是把它**吞掉**。
证据与复盘：[.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md)（英文）

**② 训练器枚举图片时不递归。**

`glob_images()` 只扫一层，所以当 `image_dir` 指向父目录时，**训练集是空的**。
一个真实的训练集有几百个子目录，因此必须**一个目录一个 subset**。
生成器产出的正是这种结构，并且通过了**训练器自己的配置校验器**的验收
（[tools/verify_toml_with_trainer.py](tools/verify_toml_with_trainer.py)）。

顺带它还会报告六类训练器会默默接受的问题：缺 caption、caption 里含 `\,`（会被读成两个 tag）、
图片大于 `max_bucket_reso`、图片带 alpha、图片太少、空目录。

## 快速开始

**两条命令。**第一次：

```cmd
setup_env.bat
```

之后每次：

```cmd
start.bat
```

`start.bat` 会在打开浏览器之前把下面这些事都做完：

- 没有 `.venv` 时，先问你要不要初始化
- 第一次问你一次**数据集根目录**，存进 `roots.txt`（已 gitignore），以后不再问
- 端口被占用就自动换一个（3001 → 3002 → …）
- **服务真的就绪之后才打开浏览器**，所以不会出现「打不开的页面」
- 服务在前台运行，Ctrl+C 结束

你也可以不用启动器：

```powershell
.\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3001
.\scripts\start.ps1 -NoBrowser
```

Linux / macOS 是**同一件事的两个脚本，对称地放在仓库根目录**：

```bash
./setup_env.sh       # 幂等；有 nvidia-smi 就装 CUDA 版 onnxruntime，否则装 CPU 版
./start.sh           # 读同一个 roots.txt，挑一个空闲端口，服务就绪后再打开浏览器
```

两者都接受 `--dry-run`：只打印将要执行的命令行，什么都不启动。

**中国大陆网络**请用国内镜像档（同一份实现，只是包源不同）：

```cmd
setup_env_cn.bat
```

```bash
./setup_env_cn.sh
```

它按 `USTC → 阿里云 → pypi.org` 的顺序回退（前一个失败才换下一个），所以某个镜像临时挂了也装得上。
实测（2026-09-19，245 MB 的 onnxruntime-gpu）：USTC **8.4–11 MB/s**，阿里云 0.86–1.25 MB/s，
pypi.org 0.55–1.01 MB/s。想换成别的镜像：设 `KOHYA_TAGGER_PIP_INDEX=https://你的/simple`
（多个用逗号分隔，按顺序回退）。

**唯一的依赖是一个专用 venv**（约 250 MB），**不需要 torch**，也不要往训练器的 venv 里装任何东西。

`setup_env.bat` 的其它用法（等价于 `scripts\setup_env.ps1`）：

```powershell
.\setup_env.bat -Gpu cuda        # 用 CUDA EP（需要 torch 或 nvidia 运行时）
.\setup_env.bat -Gpu cpu
.\setup_env.bat -Index cn        # 国内镜像档（等价于 setup_env_cn.bat）
.\setup_env.bat -DryRun          # 只打印会执行的选择，不装任何东西
.\setup_env.bat -Recreate        # 重建 venv（先打印绝对路径并征求确认）
```

## GPU

本机实测（RTX 4070 Ti SUPER，16 张真实图片，预热后取中位数）：

| 提供程序 | 中位数 | 1653 张图 |
|---|---|---|
| CPU | 0.721 秒/张 | 19.9 分钟 |
| **DirectML**（默认） | **0.192 秒/张** | **5.3 分钟** |
| CUDA | 0.168 秒/张 | 4.6 分钟 |

Windows 上默认用 DirectML：50 MB、自带、不挑厂商、不需要 CUDA/cuDNN。
CUDA 快 14%，但要多 195 MB，还多一个 torch 依赖。
**装 onnxruntime 必须显式钉住 `--index-url`**（两个安装脚本都已经这么做了）：
回退到全局 pip 源可能慢到「看起来卡死」（本机实测 0.07 MB/s）。常规档用 `pypi.org`，
国内档（`setup_env_cn.*`）按 USTC → 阿里云 → pypi.org 回退。

装完一定用它自检，而且**不要**相信 `get_available_providers()`——
提供程序加载失败时 onnxruntime 会**静默回退到 CPU**，推理照跑，只是慢 4 倍：

```powershell
.\.venv\Scripts\python.exe tools\check_provider.py    # 用真实模型建一个 session，报告**实际**生效的提供程序
```

## 模型下载

选中一个本地没有的 tagger 模型时，它会自动下载。端点顺序为
`KOHYA_TAGGER_HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com`（国内镜像；其清单已验证与主站逐字一致）。

**最省事的办法是在界面里改**（见下一节）：tagger 面板 →「模型目录…」，加进去**立即生效**，
不用重启，并写回 `model_paths.txt`。另外三种等价做法：

```powershell
# 1. 写进 model_paths.txt（推荐；一行一个，# 开头是注释，分号也可以）——服务启动时读取
# 2. 环境变量（追加，不是替换）：
set KOHYA_TAGGER_EXTRA_MODELS=D:\my-taggers;E:\more
# 3. 直接跑 CLI（启动器**没有** -ExtraModels 参数，别照抄旧文档）：
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --extra-models "D:\my-taggers"
```

> `scripts\start.ps1` / `scripts\start.sh` 只做「挑端口 + 读 roots.txt + 起服务」；
> 模型搜索目录**不会由启动器转发**（§3.7）——一旦变成命令行参数，在界面里删掉的一行
> 下次启动又会被推回来。

注意它和 `--models` / `KOHYA_TAGGER_MODELS` 的区别：**那个替换整个搜索列表**（一设就丢掉仓库的 `models/`
和所有自动发现的位置），而**这个是追加**——「再加一个目录」要的是后者。

**搜索根的先后就是优先级**：仓库 `models/` → 你加的（`model_paths.txt`）→ 我们下载的 → HF 缓存。
显式指定的永远排在自动发现的前面。
默认列表里**没有**任何「只存在于某台机器」的路径——你的 webui / ComfyUI 模型放在哪，就写进 `model_paths.txt`。

**下载不通也没关系**：把 `model.onnx` 和同目录的 `*.csv` 放进仓库的

    models/<model-id>/

（那个目录里有一个占位文件，文件名就是这句话）。它既是**默认搜索根的第 0 位**，也是**下载目的地**——
手放的和自动下载的在同一处，不会出现第二份拷贝。

## 切换数据集 / 添加模型目录：不用重启

这两类路径以前只能用命令行参数或配置文件设置，**而且只在启动时读一次**。现在两类都能在界面里改，立即生效：

| 你想做什么 | 在哪里 | 效果 |
|---|---|---|
| 切换 / 添加数据集目录 | 左栏「根目录」标题旁的 **+** | 立刻可浏览；写回 `roots.txt` |
| 删除数据集目录 | 每个根目录后面的 **×** | 最后一个删不掉（一个都不剩就打不开任何东西） |
| 添加模型扫描目录 | tagger 面板 →「模型目录…」 | 立刻出现在模型下拉框里；写回 `model_paths.txt` |
| 删除模型扫描目录 | 同一个对话框里每项的「删除」 | 自动发现的那几个只在本次运行生效；界面会说明 |

浏览器给不了原生目录选择器，所以**请粘贴绝对路径**（为了这一个控件开一个「列出任意目录」的端点，等于废掉白名单）。目录必须已经存在——配一个不存在的根，只会让每个请求都 403/404。

写文件失败时这个功能**照样能用**，但界面会直接说「重启后这条会丢」，而不是假装成功了。

## 验收

`pytest acceptance/` 里是**独立于实现、照着规格写**的判据。其中 A23 由**真实消费方**打分：
它把生成的 `dataset.toml` 喂给训练器自己的 `config_util`，要求它按目录建出 subset。

## 开发

先读 [AGENTS.md](AGENTS.md)——常驻索引，指向 `.github/memory/` 下的二级索引（英文）。
工作流程在 [.agents/skills/kohya-dataset-tagger-workflow/SKILL.md](.agents/skills/kohya-dataset-tagger-workflow/SKILL.md)（英文）。

```powershell
.\.venv\Scripts\python.exe tools\check_relevant.py          # 内环：只跑覆盖本次改动的测试
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # 汇报前的闸门：全量套件 + 文档闸门
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto       # 全量单元套件，多核并行
.\.venv\Scripts\python.exe -m pytest acceptance/ -q         # 验收判据（需要真实数据集）
.\.venv\Scripts\python.exe tools\e2e_smoke.py               # 真实 HTTP 端到端（需要服务在跑）
.\.venv\Scripts\python.exe tools\dataset_manifest.py --root "<dataset>" --out snapshot.json   # 存一份你自己的指纹
```

`local_paths.ini` 是一个检出找到自己训练集、模型和训练器的地方；随仓库提交的模板指向 `test/fixtures/sample_dataset/` 下的样例数据集，所以套件开箱即跑。
[CONTRIBUTING.md](CONTRIBUTING.md)（英文）解释了每个键，并列出了 CI 会跑什么：
[.github/workflows/ci.yml](.github/workflows/ci.yml) 覆盖 Linux 与 Windows，把依赖 huggingface.co 的判据留给每周一次的定时任务。

## 文档结构

    AGENTS.md                          常驻索引（≤ 28 KB，由闸门强制）
    .github/memory/MEMORY.md           分类表
    .github/memory/INDEX-<category>.md 主题清单
    .github/memory/<topic>.md          正文
    .github/memory/p0-spec.md          **P0 冻结契约与验收判据**（改代码前必读）
    .agents/skills/<name>/SKILL.md     工作流程

`test/test_docs_index.py` 防止这套结构烂掉：预算、链接可达、没有孤儿主题、skill 元数据合规。

## 参与贡献

欢迎提 issue 和 PR，中英文都可以。[CONTRIBUTING.md](CONTRIBUTING.md) 里有环境搭建、两级闸门、改动必须遵守的规则，以及 CI 跑什么。
安全问题请走 [SECURITY.md](SECURITY.md)；项目遵循 [Contributor Covenant](CODE_OF_CONDUCT.md)。

## 许可证

[MIT](LICENSE) © 2026 AmethystLuna。
