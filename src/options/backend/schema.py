"""TSETMC REST API endpoint paths and field documentation."""

# API endpoint paths (POST unless noted)
ENDPOINTS = {
    "login": "/Account/Login",
    "change_password": "/Account/ChangePassword",
    "option": "/Derivative/Option",
    "instrument": "/Instrument/Instrument",
    "trade_last_day": "/Trade/TradeLastDay",
    "client_type_all": "/ClientType/ClientTypeAll",
    "client_type_by_ins": "/ClientType/ClientTypeByInsCode",
    "client_type_by_date": "/ClientType/ClientTypeByDate",
}

# Market flow codes
FLOW = {
    "GENERAL": 0,
    "BOURSE": 1,
    "FARABOURSE": 2,
    "ATI": 3,  # derivatives / options market
    "PAYE_BOURSE": 4,
    "PAYE_FARABOURSE": 5,
}

# Option response fields (Derivative/Option)
OPTION_FIELDS = {
    "InsCode": "instrument code",
    "InstrumentID": "instrument id string",
    "BuyOP": "buy open positions",
    "YesterdayOP": "yesterday open positions",
    "SellOP": "sell open positions",
    "ContractSize": "contract size",
    "StrikePrice": "strike price",
    "UAInsCode": "underlying asset ins code",
    "BeginDate": "contract begin date (YYYYMMDD)",
    "EndDate": "contract end date (YYYYMMDD)",
    "AFactor": "A factor",
    "BFactor": "B factor",
    "CFactor": "C factor",
}

# ClientType fields: N = natural (حقیقی), I = legal (حقوقی)
CLIENT_TYPE_FIELDS = {
    "RecDate": "record date",
    "InsCode": "instrument code",
    "Buy_N_Volume": "natural buy volume",
    "Buy_I_Volume": "legal buy volume",
    "Buy_N_Value": "natural buy value (money)",
    "Buy_I_Value": "legal buy value (money)",
    "Buy_Count_ClientN": "natural buyer count",
    "Buy_Count_ClientI": "legal buyer count",
    "Sell_N_Volume": "natural sell volume",
    "Sell_I_Volume": "legal sell volume",
    "Sell_N_Value": "natural sell value (money)",
    "Sell_I_Value": "legal sell value (money)",
    "Sell_Count_ClientN": "natural seller count",
    "Sell_Count_ClientI": "legal seller count",
}

# Error codes requiring re-login or password change
ERROR_RELOGIN = {-3, -104, 401}
ERROR_CHANGE_PASSWORD = {107}
ERROR_BAD_CREDENTIALS = {-102}
