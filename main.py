# ================================================================
# IQ OPTION BOT V1.3 + DASHBOARD WEB (TRADINGVIEW CHARTS)
# PRICE ACTION + EXAUSTÃO + S/R + COOLDOWN
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
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()
TIMEFRAME = 60
HISTORICO_CANDLES = 100
SR_PERIODO = 20
EXAUSTAO_FATOR_TAMANHO = 1.5
MIN_PAVIO_RATIO = 0.35
COOLDOWN_VELAS = 3
STREAM_MAXDICT = 20
EXPIRACAO_SEGUNDOS = TIMEFRAME

# ================================================================
# ESTADO GLOBAL
# ================================================================

ultimo_sinal_timestamp = 0
CANDLES_BUFFER = []  # Armazena histórico em memória para a Web
SINAIS_BUFFER = []   # Armazena sinais recentes para a Web

# ================================================================
# GOOGLE SHEETS & CREDENCIAIS
# ================================================================

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("IQOPTION-BOT-V1.3")


# ================================================================
# TEMPLATE HTML / DASHBOARD DAY TRADER (TRADINGVIEW)
# ================================================================

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IQ Option Bot - Live Chart</title>
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body {
            margin: 0;
            padding: 0;
            background-color: #131722;
            color: #d1d4dc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            display: flex;
            flex-direction: column;
            height: 100vh;
        }
        header {
            background-color: #1e222d;
            padding: 12px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #2a2e39;
        }
        .title {
            font-size: 18px;
            font-weight: bold;
            color: #2962ff;
        }
        .info {
            font-size: 14px;
            color: #787b86;
        }
        #chart-container {
            flex: 1;
            width: 100%;
            height: 100%;
        }
    </style>
</head>
<body>
    <header>
        <div class="title">📈 IQ OPTION BOT - DASHBOARD M1</div>
        <div class="info">Ativo: <strong id="par-name">EURUSD</strong> | Timeframe: <strong>M1</strong></div>
    </header>
    <div id="chart-container"></div>

    <script>
        const chartContainer = document.getElementById('chart-container');
        const chart = LightweightCharts.createChart(chartContainer, {
            layout: {
                backgroundColor: '#131722',
                textColor: '#d1d4dc',
            },
            grid: {
                vertLines: { color: '#2a2e39' },
                horzLines: { color: '#2a2e39' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: '#2a2e39',
            },
            timeScale: {
                borderColor: '#2a2e39',
                timeVisible: true,
                secondsVisible: false,
            },
        });

        const candleSeries = chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });

        function atualizarDados() {
            fetch('/api/candles')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('par-name').innerText = data.par;
                    const formattedCandles = data.candles.map(c => ({
                        time: c.timestamp,
                        open: c.open,
                        high: c.high,
                        low: c.low,
                        close: c.close
                    }));
                    candleSeries.setData(formattedCandles);
                })
                .catch(err => console.error('Erro ao buscar candles:', err));
        }

        atualizarDados();
        setInterval(atualizarDados, 2000);

        window.addEventListener('resize', () => {
            chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
        });
    </script>
</body>
</html>
"""


# ================================================================
# SERVIDOR HTTP COM API E DASHBOARD
# ================================================================

class WebDashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))

        elif self.path == "/api/candles":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            payload = {
                "par": PAR,
                "candles": CANDLES_BUFFER
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def iniciar_servidor_http():
    porta = int(os.getenv("PORT", 8080))
    servidor = HTTPServer(("0.0.0.0", porta), WebDashboardHandler)
    log.info(f"Dashboard Day Trader ativo em http://0.0.0.0:{porta}")
    servidor.serve_forever()


# ================================================================
# CABEÇALHOS GOOGLE SHEETS
# ================================================================

HEADER_COLETAS = ["datetime", "par", "open", "high", "low", "close", "volume"]
HEADER_SINAIS = ["datetime_sinal", "par", "estrategia", "pavio_ratio", "direcao", "datetime_entrada", "preco_entrada", "datetime_expiracao", "preco_expiracao", "resultado", "saldo_wl", "status"]
HEADER_RESUMO = ["estrategia", "total_sinais", "wins", "losses", "empates", "assertividade", "saldo_wl", "maior_loss", "atual_loss"]


# ================================================================
# UTILITÁRIOS E TEMPO
# ================================================================

def smart_sleep_proximo_minuto():
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
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(TZ_LOCAL)

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
        raise RuntimeError("Variáveis de ambiente ausentes: " + ", ".join(faltando))


# ================================================================
# GOOGLE SHEETS
# ================================================================

def conectar_google():
    log.info("Conectando ao Google Sheets...")
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
    try:
        resposta = requests.post(url, json=payload, timeout=15)
        return resposta.status_code == 200
    except Exception as e:
        log.warning(f"Erro enviando Telegram: {e}")
        return False


# ================================================================
# ESTRATÉGIA
# ================================================================

def calcular_suporte_resistencia(historico, periodo=20):
    if len(historico) < periodo:
        return None, None
    velas_recentes = historico[-periodo:]
    suporte = min(c["low"] for c in velas_recentes)
    resistencia = max(c["high"] for c in velas_recentes)
    return suporte, resistencia

def gerar_sinal_estrategia(historico, timestamp_atual):
    global ultimo_sinal_timestamp
    if len(historico) < (SR_PERIODO + 10):
        return "NEUTRO", None

    if timestamp_atual - ultimo_sinal_timestamp < (COOLDOWN_VELAS * TIMEFRAME):
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
    media_tamanho = (sum(tamanhos_anteriores) / len(tamanhos_anteriores)) if tamanhos_anteriores else 0.0001

    eh_exaustao = tamanho_total >= (media_tamanho * EXAUSTAO_FATOR_TAMANHO)

    pavio_superior = maxima - max(abertura, fechamento)
    pavio_inferior = min(abertura, fechamento) - minima

    ratio_pavio_sup = pavio_superior / tamanho_total
    ratio_pavio_inf = pavio_inferior / tamanho_total

    if eh_exaustao and maxima >= resistencia and fechamento > abertura and ratio_pavio_sup >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "VENDA", round(ratio_pavio_sup * 100, 2)

    if eh_exaustao and minima <= suporte and fechamento < abertura and ratio_pavio_inf >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "COMPRA", round(ratio_pavio_inf * 100, 2)

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
        dados_historico = api.get_candles(PAR, TIMEFRAME, 30, int(time.time()))
        if dados_historico and len(dados_historico) >= 2:
            candles = [normalizar_candle(c["from"], c) for c in dados_historico if "from" in c]
            candles.sort(key=lambda x: x["timestamp"])
            return candles
    except Exception as e:
        log.warning(f"Erro na coleta precisa via API: {e}")
    return []

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
    linha = [candle["datetime"], PAR, candle["open"], candle["high"], candle["low"], candle["close"], candle["volume"]]
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
        datetime_sinal, PAR, "Price Action + Exaustao", f"{pavio_ratio}%", direcao,
        datetime_entrada, preco_entrada, datetime_expiracao, "", "AGUARDANDO", "", "ABERTO"
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        log.info(f"🚨 NOVO SINAL | {direcao} | ENTRADA: {datetime_entrada}")
        telegram_enviar(f"🚨 NOVO SINAL M1\n\nPar: {PAR}\nDireção: {direcao}\nEntrada: {datetime_entrada}\nPreço: {preco_entrada}\nExpiração: {datetime_expiracao}\nPavio: {pavio_ratio}%")
        return True
    except Exception as e:
        log.warning(f"Erro criando sinal: {e}")
        return False

def determinar_resultado(direcao, entrada, saida):
    entrada, saida = para_float(entrada), para_float(saida)
    if saida == entrada:
        return "EMPATE"
    if direcao == "COMPRA":
        return "WIN" if saida > entrada else "LOSS"
    if direcao == "VENDA":
        return "WIN" if saida < entrada else "LOSS"
    return "EMPATE"

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
            dt_entrada = datetime.strptime(sinal["datetime_entrada"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL)
            ts_expiracao = int(dt_entrada.timestamp()) + EXPIRACAO_SEGUNDOS

            if ts_fechado >= ts_expiracao:
                entrada = para_float(sinal["preco_entrada"])
                saida = para_float(preco_saida)
                resultado = determinar_resultado(sinal["direcao"], entrada, saida)
                linha = sinal["linha"]

                if not linha:
                    continue

                saldo_wl = {"WIN": 1, "LOSS": -1, "EMPATE": 0}.get(resultado, 0)
                aba_sinais.update(
                    range_name=f"H{linha}:L{linha}",
                    values=[[candle_fechado["datetime"], saida, resultado, saldo_wl, "FECHADO"]],
                    value_input_option="USER_ENTERED"
                )

                sinal["status"] = "FECHADO"
                sinal["resultado"] = resultado
                sinal["saldo_wl"] = saldo_wl
                alterados += 1

                emoji = {"WIN": "✅", "LOSS": "❌", "EMPATE": "⚪"}.get(resultado, "⚪")
                log.info(f"{emoji} RESULTADO | {resultado} | Direção: {sinal['direcao']}")
                telegram_enviar(f"{emoji} RESULTADO M1\n\nPar: {PAR}\nDireção: {sinal['direcao']}\nEntrada: {entrada}\nExpiração: {saida}\nResultado: {resultado}")
        except Exception as e:
            log.exception(f"Erro fechando sinal {dt_sinal}: {e}")

    return alterados


# ================================================================
# RESUMO
# ================================================================

def calcular_resumo(sinais):
    wins, losses, empates = 0, 0, 0
    atual_loss, maior_loss = 0, 0

    sinais_ordenados = sorted(sinais.values(), key=lambda x: x.get("datetime_sinal", ""))

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
        "total": total, "wins": wins, "losses": losses, "empates": empates,
        "assertividade": assertividade, "saldo": saldo, "maior_loss": maior_loss, "atual_loss": atual_loss
    }

def atualizar_resumo(aba_resumo, sinais):
    resumo = calcular_resumo(sinais)
    linha = ["Price Action + Exaustao", resumo["total"], resumo["wins"], resumo["losses"], resumo["empates"], round(resumo["assertividade"], 2), resumo["saldo"], resumo["maior_loss"], resumo["atual_loss"]]
    try:
        aba_resumo.update(range_name="A1:I2", values=[HEADER_RESUMO, linha], value_input_option="USER_ENTERED")
    except Exception as e:
        log.warning(f"Erro atualizando resumo: {e}")
    return resumo


# ================================================================
# MAIN
# ================================================================

def main():
    global CANDLES_BUFFER

    # Servidor Web com Dashboard HTTP
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

    # Popula o buffer do gráfico Web com o histórico inicial
    CANDLES_BUFFER = historico.copy()

    telegram_enviar(f"🤖 IQ OPTION BOT V1.3 + WEB DASHBOARD ONLINE\n\nPar: {PAR}\nTimeframe: M1")
    log.info("IQ OPTION BOT V1.3 + WEB DASHBOARD ONLINE")

    ultimo_timestamp_processado = None

    while True:
        try:
            smart_sleep_proximo_minuto()

            if not api.check_connect():
                log.warning("Conexão perdida. Reconectando...")
                api = conectar_iq()
                iniciar_stream(api)
                continue

            candles_stream = obter_stream_candles(api)
            if not candles_stream:
                continue

            # Atualiza o buffer global lido pela interface HTTP
            CANDLES_BUFFER = candles_stream.copy()

            timestamp_atual = candles_stream[-1]["timestamp"]

            if ultimo_timestamp_processado is None:
                ultimo_timestamp_processado = timestamp_atual
                continue

            if timestamp_atual > ultimo_timestamp_processado:
                mapa = {c["timestamp"]: c for c in candles_stream}
                ts_fechada = timestamp_atual - TIMEFRAME
                vela_fechada = mapa.get(ts_fechada)
                vela_entrada = mapa.get(timestamp_atual)

                if vela_fechada:
                    if ts_fechada not in timestamps_coletas:
                        if registrar_candle(aba_coletas, vela_fechada):
                            timestamps_coletas.add(ts_fechada)
                            log.info(f"📊 CANDLE REGISTRADO | {vela_fechada['datetime']} | O={vela_fechada['open']} | C={vela_fechada['close']}")

                    sinais = carregar_sinais(aba_sinais)
                    if atualizar_resultados(aba_sinais, sinais, vela_fechada) > 0:
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                    historico = [c for c in historico if c["timestamp"] != vela_fechada["timestamp"]]
                    historico.append(vela_fechada)
                    historico.sort(key=lambda x: x["timestamp"])
                    historico = historico[-HISTORICO_CANDLES:]

                    sinal, pavio_ratio = gerar_sinal_estrategia(historico, timestamp_atual)

                    if sinal in ("COMPRA", "VENDA") and vela_entrada:
                        criar_sinal_simulado(aba_sinais, vela_fechada, vela_entrada, pavio_ratio, sinal, sinais)
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                    ultimo_timestamp_processado = timestamp_atual

        except Exception as e:
            log.exception(f"Erro no loop principal: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
