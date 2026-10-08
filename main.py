# ================================================================
# IQ OPTION BOT V1.5
# SUPORTE/RESISTÊNCIA DINÂMICO + ADX + RSI + PAVIO
#
# MODO:
#   PAPER TRADING / SIMULADOR
#
# MERCADO ANALISADO:
#   EUR/USD
#
# TIMEFRAME:
#   M1
#
# MODELO:
#   OPERAÇÃO BINÁRIA/DIGITAL SIMULADA
#
# DIREÇÕES:
#   COMPRA
#   VENDA
#
# EXPIRAÇÃO:
#   1 MINUTO
#
# NÃO ENVIA ORDENS REAIS
# ================================================================

import os
import time
import json
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# CONFIGURAÇÕES DA ESTRATÉGIA (S&R DINÂMICO + ADX + RSI)
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

# 60 segundos = M1
TIMEFRAME = 60

# Quantidade de candles mantidos no histórico para cálculo dos indicadores
HISTORICO_CANDLES = 100

# Parâmetros do Setup Campeão (Suporte/Resistência Dinâmico)
LOOKBACK_SR = 15      # Janela para Máxima e Mínima recente
ADX_PERIODO = 14
ADX_MAX = 28.0        # Trava de consolidação (ADX < 28)

RSI_PERIODO = 14
RSI_COMPRA = 35.0     # Sobrevenda em S&R
RSI_VENDA = 65.0      # Sobrecompra em S&R

MIN_PAVIO_RATIO = 0.08  # Pavio de Rejeição mínimo de 8% (0.08)

COOLDOWN_VELAS = 2    # Intervalo de 2 velas após um sinal

# Quantidade de candles mantidos no stream em tempo real
STREAM_MAXDICT = 20

# ================================================================
# EXPIRAÇÃO DA OPERAÇÃO
# ================================================================

EXPIRACAO_SEGUNDOS = TIMEFRAME

# ================================================================
# ESTADO
# ================================================================

ultimo_sinal_timestamp = 0

# ================================================================
# GOOGLE SHEETS
# ================================================================

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1uuw_jS5-e4dUQ28DCffknMaMnJfUbekHLkTFp0aqGtA"
)

ABA_COLETAS = "IQOption_Coletas"
ABA_SINAIS = "Sinais_Bot"
ABA_RESUMO = "Resumo"

# ================================================================
# CREDENCIAIS
# ================================================================

IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ================================================================
# FUSO
# ================================================================

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")


# ================================================================
# LOG
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IQOPTION-BOT-V1.5")


# ================================================================
# SERVIDOR HTTP (Health Check + Gráfico de Velas TradingView)
# ================================================================

class DummyHTTPHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        html_grafico = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>IQ Option Bot - Gráfico M1 ({PAR})</title>
            <style>
                body, html {{ margin: 0; padding: 0; width: 100%; height: 100%; background-color: #131722; overflow: hidden; }}
                .tradingview-widget-container {{ width: 100%; height: 100vh; }}
            </style>
        </head>
        <body>
            <div class="tradingview-widget-container">
              <div id="tradingview_chart" style="width:100%;height:100%;"></div>
              <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
              <script type="text/javascript">
              new TradingView.widget({{
                "autosize": true,
                "symbol": "FX:{PAR}",
                "interval": "1",
                "timezone": "America/Sao_Paulo",
                "theme": "dark",
                "style": "1",
                "locale": "br",
                "toolbar_bg": "#f1f3f6",
                "enable_publishing": false,
                "hide_legend": false,
                "save_image": false,
                "container_id": "tradingview_chart"
              }});
              </script>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html_grafico.encode("utf-8"))

    def log_message(self, format, *args):
        return


def iniciar_servidor_http():
    porta = int(os.getenv("PORT", 8080))
    servidor = HTTPServer(("0.0.0.0", porta), DummyHTTPHandler)
    log.info(f"Servidor HTTP ativo com Gráfico na porta {porta}")
    servidor.serve_forever()


# ================================================================
# CABEÇALHOS GOOGLE SHEETS
# ================================================================

HEADER_COLETAS = [
    "datetime",
    "par",
    "open",
    "high",
    "low",
    "close",
    "volume"
]

HEADER_SINAIS = [
    "datetime_sinal",
    "par",
    "estrategia",
    "pavio_ratio",
    "direcao",
    "datetime_entrada",
    "preco_entrada",
    "datetime_expiracao",
    "preco_expiracao",
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
# UTILITÁRIOS E SINCRONIZAÇÃO DE TEMPO
# ================================================================

def smart_sleep_proximo_minuto():
    """Garante que o script 'acorde' exatamente no segundo 00.050 do próximo minuto."""
    agora = datetime.now()
    segundos_espera = 60 - agora.second - (agora.microsecond / 1_000_000.0) + 0.050
    if segundos_espera > 0:
        time.sleep(segundos_espera)


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


# ================================================================
# VALIDAÇÃO
# ================================================================

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
        raise RuntimeError(
            "Variáveis de ambiente ausentes: " + ", ".join(faltando)
        )


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
        log.warning(f"Erro verificando cabeçalho {aba.title}: {e}")


# ================================================================
# TELEGRAM
# ================================================================

def telegram_enviar(mensagem):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem
    }

    try:
        resposta = requests.post(url, json=payload, timeout=15)
        return resposta.status_code == 200
    except Exception as e:
        log.warning(f"Erro enviando Telegram: {e}")
        return False


# ================================================================
# CÁLCULOS TÉCNICOS (RSI E ADX)
# ================================================================

def calcular_rsi(fechamentos, periodo=14):
    """Calcula o RSI de 14 períodos sobre uma lista de fechamentos."""
    if len(fechamentos) < periodo + 1:
        return None

    ganhos = []
    perdas = []

    for i in range(1, len(fechamentos)):
        diferenca = fechamentos[i] - fechamentos[i - 1]
        if diferenca > 0:
            ganhos.append(diferenca)
            perdas.append(0.0)
        else:
            ganhos.append(0.0)
            perdas.append(abs(diferenca))

    if len(ganhos) < periodo:
        return None

    media_ganho = sum(ganhos[-periodo:]) / periodo
    media_perda = sum(perdas[-periodo:]) / periodo

    if media_perda == 0:
        return 100.0

    rs = media_ganho / media_perda
    return 100.0 - (100.0 / (1.0 + rs))


def calcular_adx(historico, periodo=14):
    """Calcula o ADX (Average Directional Index) de 14 períodos."""
    if len(historico) < (periodo * 2):
        return None

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, len(historico)):
        h = historico[i]["high"]
        l = historico[i]["low"]
        prev_h = historico[i - 1]["high"]
        prev_l = historico[i - 1]["low"]
        prev_c = historico[i - 1]["close"]

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        up_move = h - prev_h
        down_move = prev_l - l

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < (periodo * 2 - 1):
        return None

    dx_list = []
    for i in range(periodo, len(tr_list) + 1):
        tr_sum = sum(tr_list[i - periodo:i])
        if tr_sum == 0:
            continue
        
        plus_di = 100 * (sum(plus_dm_list[i - periodo:i]) / tr_sum)
        minus_di = 100 * (sum(minus_dm_list[i - periodo:i]) / tr_sum)

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx = 0.0
        else:
            dx = 100 * (abs(plus_di - minus_di) / di_sum)
        
        dx_list.append(dx)

    if len(dx_list) < periodo:
        return None

    adx = sum(dx_list[-periodo:]) / periodo
    return adx


# ================================================================
# ESTRATÉGIA S&R DINÂMICO + ADX + RSI + PAVIO
# ================================================================

def gerar_sinal_estrategia(historico, timestamp_atual):
    global ultimo_sinal_timestamp

    if len(historico) < (ADX_PERIODO * 2 + 5):
        return "NEUTRO", None

    if (timestamp_atual - ultimo_sinal_timestamp) < (COOLDOWN_VELAS * TIMEFRAME):
        return "NEUTRO", None

    fechamentos = [c["close"] for c in historico]
    
    rsi = calcular_rsi(fechamentos, RSI_PERIODO)
    adx = calcular_adx(historico, ADX_PERIODO)

    if rsi is None or adx is None:
        return "NEUTRO", None

    candles_recientes = historico[-(LOOKBACK_SR + 1):-1]
    if len(candles_recientes) < LOOKBACK_SR:
        return "NEUTRO", None

    max_recente = max(c["high"] for c in candles_recientes)
    min_recente = min(c["low"] for c in candles_recientes)

    vela_atual = historico[-1]
    abertura = vela_atual["open"]
    fechamento = vela_atual["close"]
    maxima = vela_atual["high"]
    minima = vela_atual["low"]

    tamanho_total = maxima - minima
    if tamanho_total == 0:
        return "NEUTRO", None

    pavio_superior = maxima - max(abertura, fechamento)
    pavio_inferior = min(abertura, fechamento) - minima

    ratio_pavio_sup = pavio_superior / tamanho_total
    ratio_pavio_inf = pavio_inferior / tamanho_total

    if (
        minima <= min_recente
        and rsi < RSI_COMPRA
        and adx < ADX_MAX
        and ratio_pavio_inf >= MIN_PAVIO_RATIO
    ):
        ultimo_sinal_timestamp = timestamp_atual
        return "COMPRA", round(ratio_pavio_inf * 100, 2)

    if (
        maxima >= max_recente
        and rsi > RSI_VENDA
        and adx < ADX_MAX
        and ratio_pavio_sup >= MIN_PAVIO_RATIO
    ):
        ultimo_sinal_timestamp = timestamp_atual
        return "VENDA", round(ratio_pavio_sup * 100, 2)

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
    log.info("IQ Option conectada | MODO PAPER TRADING")
    return api


def iniciar_stream(api):
    api.start_candles_stream(PAR, TIMEFRAME, STREAM_MAXDICT)
    time.sleep(2)


def obter_stream_candles(api):
    try:
        dados_historico = api.get_candles(PAR, TIMEFRAME, 3, int(time.time()))
        if dados_historico and len(dados_historico) >= 2:
            candles = [
                normalizar_candle(c["from"], c)
                for c in dados_historico
                if "from" in c
            ]
            candles.sort(key=lambda x: x["timestamp"])
            return candles
    except Exception as e:
        log.warning(f"Erro na coleta precisa via API: {e}")

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
        candles = [
            normalizar_candle(c["from"], c)
            for c in dados
            if "from" in c
        ]
        candles.sort(key=lambda x: x["timestamp"])
        return candles
    except Exception as e:
        log.warning(f"Erro obtendo histórico: {e}")
        return []


# ================================================================
# GOOGLE SHEETS - COLETAS
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
        candle["close"],
        candle["volume"]
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        log.warning(f"Erro registrando candle: {e}")
        return False


# ================================================================
# SINAIS
# ================================================================

def carregar_sinais(aba):
    try:
        valores = aba.get_all_values()
        sinais = {}

        for numero_linha, row in enumerate(valores[1:], start=2):
            if len(row) < 12:
                continue

            sinais[row[0]] = {
                "linha": numero_linha,
                "datetime_sinal": row[0],
                "par": row[1],
                "estrategia": row[2],
                "pavio_ratio": row[3],
                "direcao": row[4],
                "datetime_entrada": row[5],
                "preco_entrada": row[6],
                "datetime_expiracao": row[7],
                "preco_expiracao": row[8],
                "resultado": row[9],
                "saldo_wl": row[10],
                "status": row[11]
            }

        return sinais
    except Exception as e:
        log.warning(f"Erro carregando sinais: {e}")
        return {}


# ================================================================
# CRIAÇÃO DO SINAL
# ================================================================

def criar_sinal_simulado(aba, candle_sinal, candle_entrada, pavio_ratio, direcao, sinais_existentes):
    datetime_sinal = candle_sinal["datetime"]

    if datetime_sinal in sinais_existentes:
        return False

    preco_entrada = candle_entrada["open"]
    timestamp_entrada = candle_entrada["timestamp"]
    timestamp_expiracao = timestamp_entrada + EXPIRACAO_SEGUNDOS

    datetime_entrada = candle_entrada["datetime"]
    datetime_expiracao = formatar_datetime(timestamp_expiracao)

    linha = [
        datetime_sinal,
        PAR,
        "S&R Dinâmico + ADX + RSI",
        f"{pavio_ratio}%",
        direcao,
        datetime_entrada,
        preco_entrada,
        datetime_expiracao,
        "",
        "AGUARDANDO",
        "",
        "ABERTO"
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")

        log.info("=" * 60)
        log.info(f"🚨 NOVO SINAL | {direcao}")
        log.info(f"PAR: {PAR}")
        log.info(f"ENTRADA: {datetime_entrada}")
        log.info(f"PREÇO ENTRADA: {preco_entrada}")
        log.info(f"EXPIRAÇÃO: {datetime_expiracao}")
        log.info(f"PAVIO REJEIÇÃO: {pavio_ratio}%")
        log.info("=" * 60)

        telegram_enviar(
            "🚨 NOVO SINAL M1 (S&R Dinâmico + ADX + RSI)\n\n"
            f"Par: {PAR}\n"
            f"Direção: {direcao}\n"
            f"Entrada: {datetime_entrada}\n"
            f"Preço: {preco_entrada}\n"
            f"Expiração: {datetime_expiracao}\n"
            f"Pavio Rejeição: {pavio_ratio}%"
        )

        return True
    except Exception as e:
        log.warning(f"Erro criando sinal: {e}")
        return False


# ================================================================
# RESULTADO DA OPERAÇÃO
# ================================================================

def determinar_resultado(direcao, entrada, saida):
    entrada = para_float(entrada)
    saida = para_float(saida)

    if saida == entrada:
        return "EMPATE"

    if direcao == "COMPRA":
        return "WIN" if saida > entrada else "LOSS"

    if direcao == "VENDA":
        return "WIN" if saida < entrada else "LOSS"

    return "EMPATE"


# ================================================================
# ATUALIZAR RESULTADOS
# ================================================================

def atualizar_resultados(aba_sinais, sinais, candle_fechado):
    if not sinais:
        return 0

    alterados = 0
    ts_fechado = candle_fechado["timestamp"]
    preco_saida = candle_fechado["close"]

    for dt_sinal, sinal in list(sinais.items()):
        if sinal.get("status") != "ABERTO":
            continue

        try:
            dt_entrada = datetime.strptime(
                sinal["datetime_entrada"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=TZ_LOCAL)

            ts_entrada = int(dt_entrada.timestamp())
            ts_expiracao = ts_entrada + EXPIRACAO_SEGUNDOS

            if ts_fechado >= ts_expiracao:
                entrada = para_float(sinal["preco_entrada"])
                saida = para_float(preco_saida)

                resultado = determinar_resultado(
                    sinal["direcao"], entrada, saida
                )

                linha = sinal["linha"]
                if not linha:
                    continue

                saldo_wl = {"WIN": 1, "LOSS": -1, "EMPATE": 0}.get(resultado, 0)

                aba_sinais.update(
                    range_name=f"H{linha}:L{linha}",
                    values=[[
                        candle_fechado["datetime"],
                        saida,
                        resultado,
                        saldo_wl,
                        "FECHADO"
                    ]],
                    value_input_option="USER_ENTERED"
                )

                sinal["status"] = "FECHADO"
                sinal["resultado"] = resultado
                sinal["saldo_wl"] = saldo_wl

                alterados += 1
                emoji = {"WIN": "✅", "LOSS": "❌", "EMPATE": "⚪"}.get(resultado, "⚪")

                log.info("=" * 60)
                log.info(f"{emoji} RESULTADO | {resultado}")
                log.info(f"Par: {PAR}")
                log.info(f"Direção: {sinal['direcao']}")
                log.info(f"Entrada: {entrada}")
                log.info(f"Expiração: {saida}")
                log.info("=" * 60)

                telegram_enviar(
                    f"{emoji} RESULTADO M1\n\n"
                    f"Par: {PAR}\n"
                    f"Direção: {sinal['direcao']}\n"
                    f"Entrada: {entrada}\n"
                    f"Expiração: {saida}\n"
                    f"Resultado: {resultado}"
                )

        except Exception as e:
            log.exception(f"Erro fechando sinal {dt_sinal}: {e}")

    return alterados


# ================================================================
# RESUMO
# ================================================================

def calcular_resumo(sinais):
    wins = 0
    losses = 0
    empates = 0
    atual_loss = 0
    maior_loss = 0

    sinais_ordenados = sorted(
        sinais.values(),
        key=lambda x: x.get("datetime_sinal", "")
    )

    for sinal in sinais_ordenados:
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
    saldo = wins - losses

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "empates": empates,
        "assertividade": assertividade,
        "saldo": saldo,
        "maior_loss": maior_loss,
        "atual_loss": atual_loss
    }


def atualizar_resumo(aba_resumo, sinais):
    resumo = calcular_resumo(sinais)

    linha = [
        "S&R Dinâmico + ADX + RSI",
        resumo["total"],
        resumo["wins"],
        resumo["losses"],
        resumo["empates"],
        round(resumo["assertividade"], 2),
        resumo["saldo"],
        resumo["maior_loss"],
        resumo["atual_loss"]
    ]

    try:
        aba_resumo.update(
            range_name="A1:I2",
            values=[HEADER_RESUMO, linha],
            value_input_option="USER_ENTERED"
        )
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

    garantir_cabecalho(aba_coletas, HEADER_COLETAS)
    garantir_cabecalho(aba_sinais, HEADER_SINAIS)
    garantir_cabecalho(aba_resumo, HEADER_RESUMO)

    timestamps_coletas = carregar_timestamps_coletas(aba_coletas)
    sinais = carregar_sinais(aba_sinais)

    api = conectar_iq()
    historico = obter_historico_inicial(api)
    iniciar_stream(api)

    telegram_enviar(
        "🤖 IQ OPTION BOT V1.5\n\n"
        "Modo: PAPER TRADING\n"
        "Estratégia: S&R Dinâmico (15) + ADX(<28) + RSI + Pavio(8%)\n"
        f"Par: {PAR}\n"
        "Timeframe: M1\n"
        "Expiração: 1 minuto\n"
        "Direções: COMPRA / VENDA"
    )

    log.info("=" * 60)
    log.info("IQ OPTION BOT V1.5 ONLINE")
    log.info("ESTRATÉGIA: S&R Dinâmico + ADX + RSI + Pavio")
    log.info(f"PAR: {PAR}")
    log.info("TIMEFRAME: M1")
    log.info("EXPIRAÇÃO: 1 MINUTO")
    log.info("MODO: PAPER TRADING")
    log.info("=" * 60)

    ultimo_timestamp_processado = None

    while True:
        try:

            # 1. ESPERA SINCRONIZADA MATEMATICAMENTE NA VIRADA DO SEGUNDO :00.050
            smart_sleep_proximo_minuto()

            # 2. CHECAGEM DE CONEXÃO
            if not api.check_connect():
                log.warning("Conexão perdida. Reconectando...")
                api = conectar_iq()
                iniciar_stream(api)
                continue

            # 3. OBTENÇÃO CIRÚRGICA DOS CANDLES
            candles_stream = obter_stream_candles(api)
            if not candles_stream:
                continue

            timestamp_atual = candles_stream[-1]["timestamp"]

            if ultimo_timestamp_processado is None:
                ultimo_timestamp_processado = timestamp_atual
                log.info(
                    "Stream inicializado | "
                    f"Último candle: {candles_stream[-1]['datetime']}"
                )
                continue

            # 4. EXECUÇÃO NA NOVA VELA
            if timestamp_atual > ultimo_timestamp_processado:
                mapa = {c["timestamp"]: c for c in candles_stream}
                ts_fechada = timestamp_atual - TIMEFRAME

                vela_fechada = mapa.get(ts_fechada)
                vela_entrada = mapa.get(timestamp_atual)

                if vela_fechada:
                    # 1. REGISTRAR CANDLE
                    if ts_fechada not in timestamps_coletas:
                        sucesso = registrar_candle(aba_coletas, vela_fechada)
                        if sucesso:
                            timestamps_coletas.add(ts_fechada)
                            log.info(
                                f"📊 CANDLE REGISTRADO | {vela_fechada['datetime']} | "
                                f"O={vela_fechada['open']} | H={vela_fechada['high']} | "
                                f"L={vela_fechada['low']} | C={vela_fechada['close']}"
                            )

                    # 2. FECHAR OPERAÇÕES EXPIRADAS
                    sinais = carregar_sinais(aba_sinais)
                    resultados = atualizar_resultados(
                        aba_sinais, sinais, vela_fechada
                    )

                    if resultados > 0:
                        sinais = carregar_sinais(aba_sinais)
                        resumo = atualizar_resumo(aba_resumo, sinais)
                        log.info(
                            f"📊 RESUMO | W={resumo['wins']} L={resumo['losses']} "
                            f"E={resumo['empates']} ACC={resumo['assertividade']:.2f}% "
                            f"SALDO={resumo['saldo']}"
                        )

                    # 3. ATUALIZAR HISTÓRICO
                    historico = [
                        c for c in historico
                        if c["timestamp"] != vela_fechada["timestamp"]
                    ]
                    historico.append(vela_fechada)
                    historico.sort(key=lambda x: x["timestamp"])
                    historico = historico[-HISTORICO_CANDLES:]

                    # 4. GERAR NOVO SINAL COM A NOVA ESTRATÉGIA
                    sinal, pavio_ratio = gerar_sinal_estrategia(
                        historico, timestamp_atual
                    )

                    # 5. CRIAR OPERAÇÃO SIMULADA
                    if sinal in ("COMPRA", "VENDA") and vela_entrada:
                        criar_sinal_simulado(
                            aba_sinais,
                            vela_fechada,
                            vela_entrada,
                            pavio_ratio,
                            sinal,
                            sinais
                        )
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                    ultimo_timestamp_processado = timestamp_atual

        except Exception as e:
            log.exception(f"Erro no loop principal: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
