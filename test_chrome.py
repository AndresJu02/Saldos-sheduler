import sys
from pathlib import Path
from main import ensure_chrome, ensure_chromedriver

chrome = ensure_chrome()
chromedriver = ensure_chromedriver()
print("Chrome:", chrome)
print("ChromeDriver:", chromedriver)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.binary_location = chrome
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")

service = Service(executable_path=chromedriver)
driver = webdriver.Chrome(service=service, options=opts)
driver.get("https://www.google.com")
print("Título:", driver.title)
driver.quit()
print("✅ Listo")