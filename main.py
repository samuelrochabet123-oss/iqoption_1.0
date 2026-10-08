# ================================================================
# IQ OPTION BOT V1.2 (PRICE ACTION + EXAUSTÃO + S/R + COOLDOWN)
# COM SERVIDOR HTTP PARA RENDER WEB SERVICE
# ================================================================

import os
import time
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# SERVIDOR HTTP (HEALTH CHECK RENDER)
# ================================================================

class DummyHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot IQ Option (Modo Sinal / Simulador) esta rodando com sucesso!")

    def log_message(self, format, *args):
        return


def iniciar_servidor_http():
    porta = int(os.getenv("PORT", 8080))
    servidor = HTTPServer(("0.0.0.0", porta), DummyHTTPHandler)
    log.info(f"Servidor HTTP ativo na porta {porta} (Render Health Check OK).")
    servidor.serve_forever()


# ================================================================
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

TIMEFRAME = 60  # M1

SR_PERIODO = 20

EXAUSTAO_FATOR_TAMANHO = 1.5  # Quantas vezes a vela precisa ser maior que a média recente
MIN_PAVIO_RATIO = 0.35        # Pavio de rejeição mínimo (35% do tamanho total)

COOLDOWN_VELAS = 3
ultimo_sinal_timestamp = 0

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

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

LOOP_SECONDS = 1
HISTORICO_CANDLES = 100


# ================================================================
# LOG
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IQOPTION-BOT-V1.2")


# ================================================================
# CABEÇALHOS
# ================================================================

HEADER_SINAIS = [
    "datetime_sinal",
    "par",
    "estrategia",
    "pavio_ratio",
    "sinal",
    "datetime_entrada",
    "entrada",
    "datetime_resultado",
    "saida",
    "resultado",
    "saldo_wl",
    "status"
]

HEADER_RESUMO = [
    "estrategia",
    "total_sinais",
    "wins",
    "losses",
    "empates",
    "assertividade",
    "saldo_wl",
    "maior_loss",
    "atual_loss"
]


# ================================================================
# UTILITÁRIOS
# ================================================================

def para_float(valor):
    if valor is None or valor == "":
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    return float(str(valor).replace(",", ".").strip())


def timestamp_para_local(timestamp):
    return datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc
    ).astimezone(TZ_LOCAL)


def formatar_datetime(timestamp):
    return timestamp_para_local(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def normalizar_candle(timestamp, candle):
    return {
        "timestamp": int(timestamp),
        "datetime": formatar_datetime(timestamp),
        "open": float(candle["open"]),
        "high": float(candle["max"]),
        "low": float(candle["min"]),
        "close": float(candle["close"]),
        "volume": float(candle.get("volume", 0))
    }


def validar_configuracao():
    obrigatorias = {
        "IQ_EMAIL": IQ_EMAIL,
        "IQ_PASSWORD": IQ_PASSWORD,
        "GOOGLE_CREDENTIALS_JSON": GOOGLE_CREDENTIALS_JSON,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID
    }

    faltando = [nome for nome, valor in obrigatorias.items() if not valor]

    if faltando:
        raise RuntimeError("Variáveis de ambiente ausentes: " + ", ".join(faltando))


# ================================================================
# GOOGLE SHEETS
# ================================================================

def conectar_google():
    log.info("Conectando ao Google Sheets...")
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    log.info(f"Planilha conectada: {spreadsheet.title}")
    return spreadsheet


def obter_aba(spreadsheet, nome):
    try:
        return spreadsheet.worksheet(nome)
    except gspread.WorksheetNotFound:
        log.info(f"Criando aba '{nome}'...")
        return spreadsheet.add_worksheet(title=nome, rows=5000, cols=20)


def garantir_cabecalho(aba, cabecalho):
    try:
        if not aba.row_values(1):
            aba.update(range_name="A1", values=[cabecalho])
    except Exception as e:
        log.warning(f"Não foi possível verificar cabeçalho da aba {aba.title}: {e}")


# ================================================================
# TELEGRAM
# ================================================================

def telegram_enviar(mensagem):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}

    try:
        resposta = requests.post(url, json=payload, timeout=15)
        return resposta.status_code == 200
    except Exception as e:
        log.warning(f"Erro enviando Telegram: {e}")
        return False


# ================================================================
# ESTRATÉGIA: PRICE ACTION + EXAUSTÃO
# ================================================================

def calcular_suporte_resistencia(historico, periodo=20):
    if len(historico) < periodo:
        return None, None
    velas_recentes = historico[-periodo:]
    return min(c["low"] for c in velas_recentes), max(c["high"] for c in velas_recentes)


def gerar_sinal_estrategia(historico, timestamp_atual):
    global ultimo_sinal_timestamp

    if len(historico) < SR_PERIODO + 10:
        return "NEUTRO", None

    if (timestamp_atual - ultimo_sinal_timestamp) < (COOLDOWN_VELAS * TIMEFRAME):
        return "NEUTRO", None

    suporte, resistencia = calcular_suporte_resistencia(historico[:-1], SR_PERIODO)
    if suporte is None or resistencia is None:
        return "NEUTRO", None

    vela_atual = historico[-1]
    velas_anteriores = historico[-11:-1]

    abertura, fechamento = vela_atual["open"], vela_atual["close"]
    maxima, minima = vela_atual["high"], vela_atual["low"]

    tamanho_total = maxima - minima
    if tamanho_total == 0:
        return "NEUTRO", None

    tamanhos_anteriores = [c["high"] - c["low"] for c in velas_anteriores]
    media_tamanho = sum(tamanhos_anteriores) / len(tamanhos_anteriores) if tamanhos_anteriores else 0.0001

    eh_exaustao = tamanho_total >= (media_tamanho * EXAUSTAO_FATOR_TAMANHO)

    pavio_superior = maxima - max(abertura, fechamento)
    pavio_inferior = min(abertura, fechamento) - minima

    ratio_pavio_sup = pavio_superior / tamanho_total
    ratio_pavio_inf = pavio_inferior / tamanho_total

    if eh_exaustao and maxima >= resistencia and fechamento > abertura and ratio_pavio_sup >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "PUT", round(ratio_pavio_sup * 100, 2)

    if eh_exaustao and minima <= suporte and fechamento < abertura and ratio_pavio_inf >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "CALL", round(ratio_pavio_inf * 100, 2)

    return "NEUTRO", round(max(ratio_pavio_sup, ratio_pavio_inf) * 100, 2)


# ================================================================
# IQ OPTION
# ================================================================

def conectar_iq():
    log.info(f"Conectando IQ Option | PAR={PAR}")
    api = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
    try:
        api.set_max_reconnect(-1)
    except Exception:
        pass

    conectado, motivo = api.connect()
    if not conectado:
        raise RuntimeError(f"Falha na conexão IQ Option: {motivo}")

    api.change_balance("PRACTICE")
    log.info("IQ Option conectada | Modo de Leitura/Análise")
    return api


def iniciar_stream(api):
    api.start_candles_stream(PAR, TIMEFRAME, STREAM_MAXDICT)
    time.sleep(2)


def obter_stream_candles(api):
    dados = api.get_realtime_candles(PAR, TIMEFRAME)
    if not dados:
        return []

    candles = []
    for timestamp, candle in dados.items():
        try:
            if "open" in candle:
                candles.append(normalizar_candle(timestamp, candle))
        except Exception:
            pass

    candles.sort(key=lambda x: x["timestamp"])
    return candles


def obter_historico_inicial(api):
    try:
        dados = api.get_candles(PAR, TIMEFRAME, HISTORICO_CANDLES, int(time.time()))
        candles = [normalizar_candle(c["from"], c) for c in dados if "from" in c]
        candles.sort(key=lambda x: x["timestamp"])
        return candles
    except Exception as e:
        log.warning(f"Erro obtendo histórico: {e}")
        return []


# ================================================================
# GOOGLE SHEETS - REGISTROS
# ================================================================

def carregar_timestamps_coletas(aba):
    try:
        valores = aba.col_values(1)
        timestamps = set()
        for valor in valores[1:]:
            if not valor or valor.lower() == "datetime":
                continue
            try:
                dt = datetime.strptime(valor.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL)
                timestamps.add(int(dt.timestamp()))
            except Exception:
                continue
        return timestamps
    except Exception:
        return set()


def registrar_candle(aba, candle):
    linha = [
        candle["datetime"],
        PAR,
        candle["open"],
        candle["high"],
        candle["low"],
        candle["close"] if candle["close"] is not None else "",
        candle["volume"]
    ]
    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def carregar_sinais(aba):
    try:
        valores = aba.get_all_values()
        sinais = {}
        for numero_linha, row in enumerate(valores[1:], start=2):
            if len(row) >= 12:
                sinais[row[0]] = {
                    "linha": numero_linha,
                    "datetime_sinal": row[0],
                    "sinal": row[4],
                    "datetime_entrada": row[5],
                    "entrada": row[6],
                    "status": row[11],
                    "pavio_ratio": row[3]
                }
        return sinais
    except Exception:
        return {}


def criar_sinal_simulado(aba, candle_fechado, candle_entrada, pavio_ratio, sinal, sinais_existentes):
    datetime_sinal = candle_fechado["datetime"]
    if datetime_sinal in sinais_existentes:
        return False

    linha = [
        candle_fechado["datetime"],
        PAR,
        "Price Action + Exaustao",
        f"{pavio_ratio}%",
        sinal,
        candle_entrada["datetime"],
        candle_entrada["open"],
        "",
        "",
        "AGUARDANDO",
        "",
        "ABERTO"
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        log.info("=" * 50)
        log.info(f"🚨 SINAL GERADO | {sinal} | Pavio Rejeição: {pavio_ratio}%")
        log.info("=" * 50)

        telegram_enviar(
            "🚨 NOVO SINAL DETECTADO\n\n"
            f"Par: {PAR} | Direção: {sinal}\n"
            f"Rejeição Pavio: {pavio_ratio}%\n"
            f"Entrada Recomendada: {candle_entrada['datetime']}\n"
            f"Preço Atual: {candle_entrada['open']}"
        )
        return True
    except Exception as e:
        log.warning(f"Erro criando sinal na planilha: {e}")
        return False


def determinar_resultado(sinal, entrada, saida):
    entrada = para_float(entrada)
    saida = para_float(saida)

    if saida == entrada:
        return "EMPATE"
    if sinal == "CALL":
        return "WIN" if saida > entrada else "LOSS"
    if sinal == "PUT":
        return "WIN" if saida < entrada else "LOSS"

    return "EMPATE"


def atualizar_resultados(aba_sinais, sinais, candle_fechado):
    if not sinais:
        return 0

    alterados = 0
    ts_fechado = candle_fechado["timestamp"]

    for dt_sinal, sinal in list(sinais.items()):
        if sinal.get("status") != "ABERTO":
            continue

        try:
            dt_entrada = datetime.strptime(
                sinal["datetime_entrada"],
                "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=TZ_LOCAL)

            ts_entrada = int(dt_entrada.timestamp())

            if ts_fechado >= ts_entrada:
                entrada = para_float(sinal["entrada"])
                saida = para_float(candle_fechado["close"])

                resultado = determinar_resultado(sinal["sinal"], entrada, saida)
                linha = sinal["linha"]

                if not linha:
                    continue

                aba_sinais.update(
                    range_name=f"H{linha}:L{linha}",
                    values=[[
                        candle_fechado["datetime"],
                        saida,
                        resultado,
                        "",
                        "FECHADO"
                    ]],
                    value_input_option="USER_ENTERED"
                )

                sinal["status"] = "FECHADO"
                alterados += 1
                emoji = {"WIN": "✅", "LOSS": "❌", "EMPATE": "⚪"}[resultado]

                telegram_enviar(
                    f"{emoji} RESULTADO DO SINAL\n\n"
                    f"Par: {PAR} | Direção: {sinal['sinal']}\n"
                    f"Entrada: {entrada} | Saída: {saida}\n"
                    f"Resultado: {resultado}"
                )

        except Exception as e:
            log.exception(f"Erro fechando sinal {dt_sinal}: {e}")

    return alterados


def calcular_resumo(sinais):
    wins, losses, empates = 0, 0, 0
    atual_loss, maior_loss = 0, 0

    for sinal in sorted(sinais.values(), key=lambda x: x["datetime_sinal"]):
        res = str(sinal.get("resultado", "")).upper()
        if res == "WIN":
            wins += 1
            atual_loss = 0
        elif res == "LOSS":
            losses += 1
            atual_loss += 1
            maior_loss = max(maior_loss, atual_loss)
        elif res == "EMPATE":
            empates += 1

    total = wins + losses + empates
    assertividade = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "empates": empates,
        "assertividade": assertividade, "saldo": wins - losses,
        "maior_loss": maior_loss, "atual_loss": atual_loss
    }


def atualizar_resumo(aba_resumo, sinais):
    resumo = calcular_resumo(sinais)
    linha = [
        "Price Action + Exaustao",
        resumo["total"], resumo["wins"], resumo["losses"], resumo["empates"],
        round(resumo["assertividade"], 2), resumo["saldo"],
        resumo["maior_loss"], resumo["atual_loss"]
    ]

    try:
        aba_resumo.update(range_name="A1:I2", values=[HEADER_RESUMO, linha], value_input_option="USER_ENTERED")
    except Exception as e:
        log.warning(f"Erro atualizando resumo: {e}")

    return resumo


# ================================================================
# MAIN
# ================================================================

def main():
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    validar_configuracao()

    spreadsheet = conectar_google()
    aba_coletas = obter_aba(spreadsheet, ABA_COLETAS)
    aba_sinais = obter_aba(spreadsheet, ABA_SINAIS)
    aba_resumo = obter_aba(spreadsheet, ABA_RESUMO)

    garantir_cabecalho(aba_sinais, HEADER_SINAIS)
    garantir_cabecalho(aba_resumo, HEADER_RESUMO)

    timestamps_coletas = carregar_timestamps_coletas(aba_coletas)
    sinais = carregar_sinais(aba_sinais)

    api = conectar_iq()
    historico = obter_historico_inicial(api)
    iniciar_stream(api)

    telegram_enviar("🤖 IQ OPTION BOT V1.2 (SINAIS M1 ONLINE)")

    ultimo_timestamp_processado = None

    while True:
        try:
            if not api.check_connect():
                api = conectar_iq()
                iniciar_stream(api)
                continue

            candles_stream = obter_stream_candles(api)
            if not candles_stream:
                time.sleep(LOOP_SECONDS)
                continue

            timestamp_atual = candles_stream[-1]["timestamp"]

            if ultimo_timestamp_processado is None:
                ultimo_timestamp_processado = timestamp_atual
                time.sleep(LOOP_SECONDS)
                continue

            if timestamp_atual > ultimo_timestamp_processado:
                mapa = {c["timestamp"]: c for c in candles_stream}
                ts_fechada = timestamp_atual - TIMEFRAME

                vela_fechada = mapa.get(ts_fechada)
                vela_entrada = mapa.get(timestamp_atual)

                if vela_fechada:
                    if ts_fechada not in timestamps_coletas:
                        sucesso = registrar_candle(aba_coletas, vela_fechada)
                        if sucesso:
                            timestamps_coletas.add(ts_fechada)
                            log.info(f"📊 CANDLE REGISTRADO | {vela_fechada['datetime']} | O={vela_fechada['open']} | C={vela_fechada['close']}")

                    sinais = carregar_sinais(aba_sinais)
                    resultados = atualizar_resultados(aba_sinais, sinais, vela_fechada)
                    if resultados > 0:
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                    historico = [c for c in historico if c["timestamp"] != vela_fechada["timestamp"]]
                    historico.append(vela_fechada)
                    historico.sort(key=lambda x: x["timestamp"])
                    historico = historico[-HISTORICO_CANDLES:]

                    sinal, pavio_ratio = gerar_sinal_estrategia(historico, timestamp_atual)

                    if sinal in ("CALL", "PUT") and vela_entrada:
                        criar_sinal_simulado(aba_sinais, vela_fechada, vela_entrada, pavio_ratio, sinal, sinais)
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                ultimo_timestamp_processado = timestamp_atual

            time.sleep(LOOP_SECONDS)

        except Exception as e:
            log.exception(f"Erro no loop principal: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
