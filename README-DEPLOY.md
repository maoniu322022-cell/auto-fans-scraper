# Windows 一键部署（auto-fans-scraper）

## 1. 安装基础环境
- Python 3.10+
- Git
- Google Chrome

## 2. 拉代码
`powershell
cd C:\Users\maoni
git clone https://github.com/maoniu322022-cell/auto-fans-scraper.git
cd .\auto-fans-scraper
`

## 3. 建虚拟环境并安装依赖
`powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
playwright install
`

## 4. 运行
`powershell
python -u .\main.py
`
