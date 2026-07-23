from base_provider import BaseProvider
import requests

class DIDWWProvider(BaseProvider):
    name = "DIDWW"
    sheet_row = 4
    sheet_col = 2

    config_fields = [
        {"key": "api_key", "label": "API Key", "type": "str", "default": "tpD8CvI2Zt$xjxEq1wj1TW5kUAW!JBwP"},
        {"key": "url", "label": "URL API", "type": "str", "default": "https://api.didww.com/v3/balance"},
    ]

    def get_balance(self, config, google_sheet, sheet_url, driver_paths=None, get_driver_fn=None):
        # DIDWW no necesita navegador
        try:
            url = config.get("url") or "https://api.didww.com/v3/balance"
            headers = {
                "Api-Key": config["api_key"],
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json"
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                saldo_raw = data.get("data", {}).get("attributes", {}).get("balance", "")
                if not saldo_raw:
                    saldo_raw = str(data)
                import re
                m = re.search(r"[-+]?\d[\d\.,]*", saldo_raw)
                if m:
                    amt = float(m.group().replace(",", ""))
                    formatted = f"$ {amt:.2f} USD"
                else:
                    formatted = saldo_raw
                google_sheet.update_cell(self.sheet_row, self.sheet_col, formatted)
                return True, formatted
            else:
                return False, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, str(e)