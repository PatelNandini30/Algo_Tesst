from typing import Dict

DATA_TYPE_METHODS: Dict[str, str] = {
    "option_data": "import_option_file",
    "spot_data": "import_spot_file",
    "expiry_calendar": "import_expiry_file",
    "trading_holidays": "import_holiday_file",
    "super_trend_segments": "import_str_file",
}
