#!/usr/bin/env bash
# 初始化 Kohya Dataset Tagger 的 Python 环境（**国内镜像档**，幂等）。
# 与 ./setup_env.sh 是同一份实现，只是带上了 --index cn：
#   USTC -> 阿里云 -> pypi.org 依次回退；不再受全局 pip 源（可能很慢）影响。
# 想换成自己的镜像：KOHYA_TAGGER_PIP_INDEX=https://你的/simple ./setup_env_cn.sh
#
# 这一层只做两件事：切到仓库根，然后把 --index cn 与参数一起交给 scripts/setup_env.sh。
set -euo pipefail
cd "$(dirname "$0")"
exec bash scripts/setup_env.sh --index cn "$@"
