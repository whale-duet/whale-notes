# Debian 12 / PEP 668 下装 Python 包：venv 路线与验证清单

- **一句话价值**：系统 pip 被 PEP 668 挡住（`externally-managed-environment`）不是环境坏了，
  是 Debian 故意的；走 `python3 -m venv` 就行，且 venv 里的 pip 会自动继承全局镜像配置。
  附一份「装完怎么确认真的装上了、且没污染系统」的四步清单。
- **适用场景**：Debian/Ubuntu 新版容器或服务器要给脚本装第三方库；批量环境初始化。
- **限制**：Debian 12 + Python 3.11 实测；`python3-venv` 的名字在别的发行版/版本可能不同；
  没测 uv/poetry 这类现代工具。

## 事实（按踩坑顺序）

1. `pip3 install <pkg>` → 被 PEP 668 拦下：`error: externally-managed-environment`。
   这是发行版的保护，**不要**第一反应加 `--break-system-packages`。
2. `python3 -m venv env` 一开始**失败**，报 `Failing command: …/bin/python3`（缺 `ensurepip`）。
   原因是没装 `python3-venv` 这个包。装它就好：
   `apt-get install -y --no-install-recommends python3-venv`。
3. 装好后 venv 一次成功。**venv 里的 pip 自动继承全局 pip 配置**（例如 `global.index-url`
   指向国内镜像），`pip config list` 能直接看到，不用再配一遍。
4. 隔离要自己确认：venv 里装了包，系统 `python3 -c "import <pkg>"` 仍应 `ModuleNotFoundError`。

## 验证清单（四条，缺一条就不算验完）

```
# 1) pip 真的在 venv 里
./env/bin/pip --version            # 路径指向 env/lib/.../site-packages

# 2) 镜像确实生效（看 Downloading 的域名，而不是只看配置）
./env/bin/pip install --no-cache-dir <pkg> 2>&1 | grep -i downloading

# 3) 包真的能用
./env/bin/python -c "import <pkg>; print(<pkg>.__version__)"

# 4) 没污染系统
python3 -c "import <pkg>"          # 应 ModuleNotFoundError

# 5)（可选）清干净
rm -rf ./env
```

## 代价与约定

- **一个空 venv 约 25 MB**（含 pip + setuptools）。别在持久目录里堆一堆闲置 venv。
- 一次性验证就建在 `/tmp`，测完删；长期用的环境建在固定位置并**在笔记里只记名字**，别记一串绝对路径。
- 加 `--no-cache-dir` 只是为了不往 pip 缓存里留 wheel，省磁盘；临时环境不加也可以。
- `--break-system-packages` 只在「确实要往系统 site-packages 装」时用，是最后手段。
- 系统工具链走 `apt`，纯 Python 库走 venv，两者别混着装进系统 python。

## 未验证

- 只在 Debian 12 / Python 3.11 上跑过；`ensurepip` 缺失的具体打包差异没深究。
- 没测「apt 装 python3-venv」与「pip 装 virtualenv」这两条路线的行为差异。
