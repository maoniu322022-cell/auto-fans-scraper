# auto-fans-scraper

用于批量处理姓名输入并执行自动化采集流程的 Python + Playwright 工具。

## 功能简介

- 从 `data/names.txt` 读取姓名（每行一个）。
- 按配置执行自动化浏览流程并采集结果。
- 输出 CSV 结果文件，支持失败重试队列。
- 记录运行日志，便于排查问题。

## 运行环境

- Windows（已提供一键脚本）
- Python 3.10+
- Google Chrome（或 Playwright 支持的浏览器）

## 安装与启动

### 方式一：Windows 一键初始化（推荐）

```powershell
git clone https://github.com/maoniu322022-cell/auto-fans-scraper.git
cd auto-fans-scraper
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
python -u .\main.py
```

### 方式二：手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
playwright install
```

创建 `.env`（可从 `.env.example` 复制）后运行：

```powershell
python -u .\main.py
```

## 输入与输出

### 输入文件

- `data/names.txt`：待处理姓名列表（每行一个姓名）

### 输出文件（默认）

- `data/results.csv`：采集结果
- `failed_queue.csv`：失败任务队列
- `run.log` 或 `logs/` 下日志文件：运行日志

> 说明：具体输出路径以 `config.py` / `.env` 配置为准，请保持两者一致。

## 项目结构（核心）

```text
auto-fans-scraper/
├─ main.py
├─ scraper.py
├─ config.py
├─ requirements.txt
├─ .env.example
├─ README.md
├─ README-DEPLOY.md
├─ setup_windows.ps1
└─ data/
   └─ names.txt
```

## 常见问题

### 1) 新电脑运行失败（依赖/浏览器问题）

```powershell
pip install -r requirements.txt
playwright install
```

### 2) Chrome 路径问题

如本机 Chrome 安装路径与代码默认值不同，请在 `scraper.py` 或配置中调整浏览器路径。

### 3) 为什么仓库里没有 `.env`、日志和结果文件？

这些属于本地运行产物或敏感配置，已在 `.gitignore` 中忽略，不应上传到仓库。

## 合规与免责声明

本项目仅用于合法、合规且已获授权的自动化测试与数据处理场景。  
请遵守目标网站服务条款、当地法律法规及隐私要求。使用者对其行为与后果负责。