# Auto Fans Scraper

自动化人物信息搜索和电话提取系统

## 功能

- 按名字从 PeopleSearchNow 搜索人物
- 自动过滤年龄 53-75 岁的目标用户
- 自动点击详情页面获取完整信息
- 提取 Wireless 电话号码
- 支持批量处理多个名字
- 自动绕过 Cloudflare 验证

## 安装

```bash
git clone https://github.com/maoniu322022-cell/auto-fans-scraper.git
cd auto-fans-scraper
pip install -r requirements.txt
```

## 依赖

- playwright: 浏览器自动化
- cloudscraper: 绕过 Cloudflare
- python-dotenv: 环境变量管理

## 使用

```bash
python main.py
```

## 项目结构

```
auto-fans-scraper/
├── README.md
├── requirements.txt
├── main.py                 # 主程序入口
├── config.py               # 配置管理
├── scraper.py              # 爬虫核心
├── data/
│   ├── names.txt           # 输入名字列表
│   └── results.csv         # 输出结果
└── logs/
    └── app.log             # 应用日志
```

## 配置

编辑 `config.py` 配置搜索参数：

```python
MIN_AGE = 53
MAX_AGE = 75
ONLY_WIRELESS = True  # 仅保留 Wireless 电话
```

## 注意

- 需要手动完成 Cloudflare 验证（如果自动绕过失败）
- 结果保存在 `data/results.csv`
- 日志文件位置: `logs/app.log`
