# ================================================================
# IQ OPTION BOT V1.2
# ================================================================
#
# MODO: SIMULADOR
# ATIVO: definido pela variável PAR
# TIMEFRAME: M1
# ESTRATÉGIA: RSI 14 - 30/70
#
# FLUXO:
#
# VELA N FECHADA
#       ↓
# RSI 14
#       ↓
# CALL / PUT
#       ↓
# ABERTURA DA VELA N+1
#       ↓
# ENTRADA SIMULADA
#       ↓
# FECHAMENTO DA VELA N+1
#       ↓
# WIN / LOSS / EMPATE
#       ↓
# GOOGLE SHEETS + TELEGRAM
#
# ATENÇÃO:
# ESTE BOT NÃO EXECUTA ORDENS REAIS.
# ================================================================

import os
import time
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

TIMEFRAME = 60

RSI_PERIODO = 14
RSI_CALL = 30
RSI_PUT = 70

STREAM_MAXDICT = 20

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1uuw_jS5-e4dUQ28DCffknMaMnJfUbekHLkTFp0aqGtA"
)

ABA_COLETAS = "IQOption_Coletas"
ABA_SINAIS = "Sinais_Bot"
ABA_RESUMO = "Resumo"

IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

GOOGLE_CREDENTIALS_JSON = os.getenv(
    "GOOGLE_CREDENTIALS_JSON"
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

LOOP_SECONDS = 1

HISTORICO_CANDLES = 100


# ================================================================
# LOG
# ================================================================

logging.basicConfig(
