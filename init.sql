CREATE TABLE users (
    id INTEGER NOT NULL,
    chat_id VARCHAR NOT NULL,
    api_key VARCHAR NOT NULL,
    min_equity FLOAT,
    min_ml FLOAT,
    max_daily_loss FLOAT,
    dd_percent FLOAT,
    heartbeat_min INTEGER,
    last_alert_at DATETIME,
    lost_conn_alerted BOOLEAN,
    PRIMARY KEY (id)
);

CREATE TABLE symbol_snapshots (
    id INTEGER NOT NULL,
    api_key VARCHAR NOT NULL,
    account_id BIGINT NOT NULL,
    symbol VARCHAR NOT NULL,
    price FLOAT,
    dd_percent FLOAT,
    buy_lots FLOAT,
    buy_count INTEGER,
    sell_lots FLOAT,
    sell_count INTEGER,
    ts DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT uix_symbol_unique UNIQUE (api_key, account_id, symbol)
);

CREATE TABLE accounts (
    id INTEGER NOT NULL,
    api_key VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    name VARCHAR,
    is_cent BOOLEAN,
    PRIMARY KEY (id),
    UNIQUE (api_key, account_id)
);

CREATE TABLE last_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key TEXT NOT NULL,
    account_id TEXT NOT NULL,
    equity REAL,
    margin_level REAL,
    pnl_daily REAL,
    balance REAL,
    ts DATETIME,
    last_seen DATETIME
);
