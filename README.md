# Auto Fans Scraper

自动化人物信息搜索系统，从 [PeopleSearchNow](https://www.peoplesearchnow.com) 按姓名批量抓取目标年龄段人员信息（姓名、年龄、位置、Wireless 电话）。

## 功能

- 按姓名从 PeopleSearchNow 搜索人物
- 自动过滤指定年龄范围内的用户（默认 53–75 岁）
- 提取 Wireless / Mobile 电话号码
- 支持批量处理多个姓名
- cloudscraper 优先 + Playwright 降级双路径抓取
- 可配置的 Cloudflare 处理策略（skip / manual / retry）
- 统一指数退避重试，重试次数与延迟均可配置
- 增量写入 + 全局去重，多次运行不重复膨胀数据
- 运行结束后输出摘要统计（总数 / 成功 / 失败 / 结果数 / 耗时）

## 安装

```bash
git clone https://github.com/maoniu322022-cell/auto-fans-scraper.git
cd auto-fans-scraper
pip install -r requirements.txt
# 安装 Playwright 浏览器（首次必须执行）
python -m playwright install chromium
```

## 配置（.env）

复制示例文件并按需编辑：

```bash
cp .env.example .env
```

`.env` 文件中的所有参数均为可选，缺失时使用 `config.py` 中的默认值：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `MIN_AGE` | `53` | 年龄下限 |
| `MAX_AGE` | `75` | 年龄上限 |
| `ONLY_WIRELESS` | `true` | 仅保留 Wireless 电话 |
| `HEADLESS` | `false` | 无头模式（服务器端设为 `true`） |
| `TIMEOUT` | `30000` | 页面加载超时（毫秒） |
| `WAIT_TIME` | `2` | 页面就绪后额外等待（秒） |
| `MAX_RETRIES` | `3` | 网络/页面动作最大重试次数 |
| `RETRY_BASE_DELAY` | `1.0` | 指数退避起始延迟（秒） |
| `CF_MODE` | `skip` | Cloudflare 策略：`skip` \| `manual` \| `retry` |
| `LOG_LEVEL` | `INFO` | 日志级别：`DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `LOG_FILE` | `logs/app.log` | 日志文件路径 |
| `INPUT_FILE` | `data/names.txt` | 输入姓名列表文件 |
| `OUTPUT_FILE` | `data/results.csv` | 输出 CSV 文件路径 |

### CF_MODE 说明

| 值 | 行为 |
|---|---|
| `skip`（默认） | 遇到 Cloudflare 直接跳过该条记录，适合无人值守批处理 |
| `manual` | 等待最多 45 秒让浏览器自动通过，超时后继续（不阻塞 STDIN） |
| `retry` | 与 `manual` 等待相同，重试由外层 `MAX_RETRIES` 控制 |

## 使用

1. 将待搜索姓名每行一个写入 `data/names.txt`：

   ```
   John Smith
   Jane Doe
   ```

2. 运行：

   ```bash
   python main.py
   ```

3. 结果保存在 `data/results.csv`，日志在 `logs/app.log`。
4. 电话号码会在列表页筛选后进入 **详情页** 提取（优先匹配 Wireless / Mobile），因此相比仅解析列表页会稍慢。

## 项目结构

```
auto-fans-scraper/
├── README.md
├── requirements.txt
├── .env.example            # 环境变量示例（复制为 .env 使用）
├── main.py                 # 主程序入口
├── config.py               # 配置管理（读取 .env + 默认值）
├── scraper.py              # 爬虫核心
├── data/
│   ├── names.txt           # 输入姓名列表
│   └── results.csv         # 输出结果（增量去重写入）
└── logs/
    └── app.log             # 应用日志
```

## 常见故障排查

### Cloudflare 验证失败
- 默认 `CF_MODE=skip` 会跳过遇到 Cloudflare 的记录；若需要尝试自动通过，改为 `CF_MODE=manual`。
- 在有头模式（`HEADLESS=false`）下运行，观察浏览器行为有助于排查。

### 超时 / 无结果
- 适当增大 `TIMEOUT`（如 `60000`）和 `WAIT_TIME`（如 `5`）。
- 增大 `MAX_RETRIES`（如 `5`）减少偶发网络失败的影响。

### 浏览器未安装
- 首次使用必须执行 `python -m playwright install chromium`。

### 结果 CSV 中出现 "待获取" / "未获取"
- cloudscraper 路径仅解析 HTML 文本，电话字段可能无法获取；Playwright 路径会尝试从页面 DOM 提取。
- Playwright 路径会继续访问每条结果的详情页提取电话；若详情页无号码、受风控或访问失败，字段会保留为 `未获取`。
- 两条路径均未能获取时，字段填 `待获取` / `未获取`。

## 注意事项

- 本工具仅用于合法的数据查询场景，请遵守目标网站的服务条款。
- 输出文件采用**增量去重写入**策略：多次运行同一名字不会产生重复行，以 `(name, age, location, phone)` 为去重键。
